"""
vesc_gantry.py

Gantry driver for the SauceBot — NodeMCU ESP8266 microcontroller over USB serial.
Implements the move_to(position_mm) interface expected by OrderManager.

The Arduino firmware (gantrymanualtestcode) handles closed-loop position control
using an encoder and ESC on-board. Python sends plain ASCII commands and waits
for tagged response lines.

Wire protocol uses inches; this module converts transparently (1 in = 25.4 mm).

USB port is auto-detected by probing CH340/FTDI/CP210x candidates for a [POS]
response. Falls back to GANTRY_PORT if auto-detection fails.

Tune TRAVEL_SPEED_IPS and SWEEP_SPEED_IPS after verifying motion on hardware:
  - TRAVEL_SPEED_IPS: normal point-to-point speed (in/s, max 6.0 on Arduino)
  - SWEEP_SPEED_IPS:  slow speed during sauce dispense sweep (in/s)
"""

import sys
import time
import serial
import serial.tools.list_ports

from pi.utils.logger import log

# ─── Config ───────────────────────────────────────────────────────────────────

# Fallback port used if auto-detection finds no gantry firmware.
GANTRY_PORT = "COM3" if sys.platform == "win32" else "/dev/ttyUSB0"
GANTRY_BAUD = 115200

# Normal point-to-point move speed (inches per second).
TRAVEL_SPEED_IPS = 1.5

# Slow speed used during the sauce dispense sweep (inches per second).
SWEEP_SPEED_IPS = 0.5

# Sentinel value imported by order_manager.py — pass as max_duty=SWEEP_MAX_DUTY
# to move_to() to select SWEEP_SPEED_IPS instead of TRAVEL_SPEED_IPS.
SWEEP_MAX_DUTY = 0.51   # kept for backward compatibility with order_manager

# Raise TimeoutError if a move doesn't complete within this many seconds.
TRAVEL_TIMEOUT_S = 30

# Time to wait for the Arduino boot sequence (LED test + 5 s encoder test + ESC arming).
BOOT_TIMEOUT_S = 20.0

# USB VID of the VESC — always skipped when scanning for the gantry Arduino.
_VESC_VID = 0x0483   # STMicroelectronics

# Exact VID+PID of the gantry controller — Silicon Labs CP210x UART Bridge.
# Confirmed from `lsusb`: Bus 003 Device 003: ID 10c4:ea60
# This device is tried first before the generic probe loop.
_GANTRY_VID = 0x10C4
_GANTRY_PID = 0xEA60

# Known non-gantry devices on the same USB hub — never probed as gantry candidates.
#   2a03:0042  Arduino Mega 2560 Rev3  → gripper / extruder (ArduinoController)
#   2341:0043  Arduino Uno R3          → conveyor belt (GPIOConveyor)
_SKIP_DEVICES = frozenset({
    (0x2A03, 0x0042),
    (0x2341, 0x0043),
})

# VIDs and description substrings associated with NodeMCU / Arduino clones.
# Used as fallback candidates if the exact VID+PID match above fails.
_ARDUINO_VIDS = frozenset({
    0x2341,  # Arduino SA
    0x2A03,  # Arduino SRL
    0x1A86,  # WCH CH340 / CH341 (NodeMCU, WeMos, clones)
    0x0403,  # FTDI FT232
    0x10C4,  # Silicon Labs CP210x
})
_ARDUINO_KEYWORDS = ('arduino', 'ch340', 'ch341', 'ftdi', 'usb serial', 'cp210')


# ─── Port detection ───────────────────────────────────────────────────────────

def _find_gantry_port() -> str:
    """
    Find the gantry serial port.

    Strategy:
      1. Return immediately if the exact VID+PID (CP210x 10c4:ea60) is found.
         No POS probe — the Arduino may still be in its 10s boot sequence and
         won't respond to commands yet. boot_check() handles the wait.
      2. For any remaining Arduino-VID candidates (unknown devices), send POS
         and confirm a [POS] response before accepting them.
      3. Fall back to GANTRY_PORT if nothing is found.

    Devices in _SKIP_DEVICES (Mega, Uno) are never touched.
    """
    all_ports = serial.tools.list_ports.comports()

    candidates = []
    for p in all_ports:
        if p.vid == _VESC_VID:
            continue
        if (p.vid, p.pid) in _SKIP_DEVICES:
            continue   # Mega (gripper/extruder) or Uno (conveyor) — not the gantry
        if p.vid == _GANTRY_VID and p.pid == _GANTRY_PID:
            log.info("VESCGantry: found CP210x gantry on %s (%s)", p.device, p.description)
            return p.device   # exact match — trust the VID+PID, skip probe
        desc = (p.description or '').lower()
        if p.vid in _ARDUINO_VIDS or any(k in desc for k in _ARDUINO_KEYWORDS):
            candidates.append(p.device)

    # Generic fallback: probe unknown candidates with POS
    def _num(name: str) -> int:
        digits = ''.join(c for c in name if c.isdigit())
        return int(digits) if digits else 999

    for port_name in sorted(candidates, key=_num):
        try:
            probe = serial.Serial(port_name, GANTRY_BAUD, timeout=2)
            time.sleep(0.1)
            probe.reset_input_buffer()
            probe.write(b'POS\n')
            probe.flush()
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if probe.in_waiting:
                    line = probe.readline().decode('utf-8', errors='ignore').strip()
                    if '[POS]' in line:
                        log.info("VESCGantry: found gantry firmware on %s", port_name)
                        probe.close()
                        return port_name
            probe.close()
        except Exception as exc:
            log.debug("VESCGantry: probe failed on %s: %s", port_name, exc)

    log.warning("VESCGantry: auto-detect failed — using %s", GANTRY_PORT)
    return GANTRY_PORT


# ─── Driver ───────────────────────────────────────────────────────────────────

class VESCGantry:
    """
    Controls the gantry belt motor via an Arduino-based ESC controller over USB.

    The Arduino firmware handles encoder-based closed-loop position control
    internally. Python sends ASCII commands and reads tagged response lines.

    Unit conversion: mm (Python API) <-> inches (Arduino wire protocol).

    move_to() is the primary interface used by OrderManager.
    home() runs the homing routine (ZERO) on the Arduino.
    start() / reverse() / stop() are available for manual jog.
    """

    def __init__(self):
        port = _find_gantry_port()
        log.info("VESCGantry: connecting to %s @ %d baud", port, GANTRY_BAUD)
        self._ser = serial.Serial(port, GANTRY_BAUD, timeout=0.5)
        self._position_mm = 0
        log.info("VESCGantry: connected")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _send(self, cmd: str) -> None:
        """Write a newline-terminated command to the Arduino."""
        self._ser.write(f"{cmd}\n".encode('utf-8'))
        self._ser.flush()
        log.debug("VESCGantry → %s", cmd)

    def _readline(self) -> str:
        """
        Read one line from the Arduino. Returns '' on timeout (0.5 s per call,
        set by the serial port timeout in __init__).
        """
        try:
            return self._ser.readline().decode('utf-8', errors='ignore').strip()
        except Exception:
            return ''

    # ── Public interface ──────────────────────────────────────────────────────

    def boot_check(self) -> None:
        """
        Wait for the Arduino boot sequence to finish, then verify with POS.

        Two cases:
          - Fresh boot: banner sequence takes ~10 s. Detected by the
            "Counts/inch" + "---" separator at the end of the banner.
          - Already running: [STATUS] lines arrive immediately. Detected
            within 2 s — skip the banner wait and go straight to POS verify.

        Raises RuntimeError if the Arduino doesn't respond within BOOT_TIMEOUT_S.
        """
        log.info("VESCGantry: waiting for Arduino boot...")
        self._ser.reset_input_buffer()

        deadline = time.monotonic() + BOOT_TIMEOUT_S
        counts_seen = False
        # Quick check: if [STATUS] lines are already arriving the Arduino is
        # already running — no need to wait for a banner that will never come.
        quick_deadline = time.monotonic() + 2.0
        while time.monotonic() < quick_deadline:
            line = self._readline()
            if not line:
                continue
            log.debug("VESCGantry boot: %s", line)
            if '[STATUS]' in line:
                log.info("VESCGantry: Arduino already running — skipping banner wait")
                self._ser.reset_input_buffer()
                self._send("POS")
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    line = self._readline()
                    if '[POS]' in line:
                        log.info("VESCGantry: boot check passed — %s", line)
                        return
                raise RuntimeError("VESCGantry: Arduino did not respond to POS command during boot check")

        # Fresh boot path: wait for banner completion marker
        while time.monotonic() < deadline:
            line = self._readline()
            if not line:
                continue
            log.debug("VESCGantry boot: %s", line)
            if 'Counts/inch' in line:
                counts_seen = True
            elif counts_seen and line.startswith('---'):
                log.info("VESCGantry: boot banner complete")
                break

        # Verify two-way comms with a POS query
        self._ser.reset_input_buffer()
        self._send("POS")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            line = self._readline()
            if '[POS]' in line:
                log.info("VESCGantry: boot check passed — %s", line)
                return
            if '[ERR]' in line:
                raise RuntimeError(f"VESCGantry boot check error: {line}")

        raise RuntimeError(
            "VESCGantry: Arduino did not respond to POS command during boot check"
        )

    def home(self) -> None:
        """
        Run the homing routine (ZERO command). Reverses to the limit switch,
        zeros the encoder, then confirms exact zero. Blocks until complete.
        Raises RuntimeError on failure or timeout (60 s).
        """
        log.info("VESCGantry: starting homing routine (ZERO)...")
        self._ser.reset_input_buffer()
        self._send("ZERO")
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            line = self._readline()
            if not line:
                continue
            log.debug("VESCGantry homing: %s", line)
            if "[ZERO] Ready." in line:
                self._position_mm = 0
                log.info("VESCGantry: homing complete, position = 0 mm")
                return
            if "[ZERO]" in line and "ERROR" in line:
                raise RuntimeError(f"VESCGantry homing failed: {line}")

        raise RuntimeError("VESCGantry: homing timed out after 60 s")

    def get_position_mm(self) -> float:
        """
        Query the Arduino for its current encoder position and return it in mm.
        Parses the [POS] X.XXX in (...) response line.
        """
        self._ser.reset_input_buffer()
        self._send("POS")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            line = self._readline()
            if '[POS]' in line:
                # Format: [POS] X.XXX in (counts) | Speed: ...
                try:
                    pos_in = float(line.split()[1])
                    return pos_in * 25.4
                except (IndexError, ValueError):
                    pass
        log.warning("VESCGantry: could not read position, returning cached value")
        return float(self._position_mm)

    def move_to(self, position_mm: int, max_duty: float = 1.0) -> None:
        """
        Closed-loop move to position_mm from the dock end of the rail.
        Sends a GOTO command to the Arduino which manages encoder-based control.

        Pass max_duty=SWEEP_MAX_DUTY for the slow sauce dispense sweep — this
        sets SWEEP_SPEED_IPS on the Arduino instead of TRAVEL_SPEED_IPS.

        Raises TimeoutError if the move doesn't complete within TRAVEL_TIMEOUT_S.
        Raises RuntimeError on Arduino-reported errors or limit conditions.
        """
        if position_mm == self._position_mm:
            return

        speed_ips  = SWEEP_SPEED_IPS if max_duty <= SWEEP_MAX_DUTY else TRAVEL_SPEED_IPS
        position_in = position_mm / 25.4

        log.info(
            "VESCGantry: move_to %d mm (%.4f in) at %.2f in/s",
            position_mm, position_in, speed_ips,
        )

        self._ser.reset_input_buffer()
        # Set speed, then clear the [SPEED] acknowledgement before issuing GOTO
        self._send(f"SPEED{speed_ips:.2f}")
        time.sleep(0.05)
        self._ser.reset_input_buffer()
        self._send(f"GOTO{position_in:.4f}")

        deadline = time.monotonic() + TRAVEL_TIMEOUT_S
        while time.monotonic() < deadline:
            line = self._readline()
            if not line:
                continue
            log.debug("VESCGantry move: %s", line)
            if "[GOTO] Arrived at" in line:
                self._position_mm = position_mm
                log.info("VESCGantry: arrived at %d mm", position_mm)
                return
            if "[GOTO] Aborted" in line:
                raise RuntimeError(f"VESCGantry move aborted: {line}")
            if "[ERR]" in line:
                raise RuntimeError(f"VESCGantry move error: {line}")

        raise TimeoutError(
            f"VESCGantry timed out after {TRAVEL_TIMEOUT_S}s moving to {position_mm}mm"
        )

    def start(self, speed: int) -> None:
        """Drive the gantry forward (away from dock). speed: 0–100."""
        power = max(0, min(100, speed))
        log.info("VESCGantry: forward speed=%d", power)
        self._send(f"FWD{power}")

    def reverse(self, speed: int) -> None:
        """Drive the gantry backward (toward dock). speed: 0–100."""
        power = max(0, min(100, speed))
        log.info("VESCGantry: reverse speed=%d", power)
        self._send(f"REV{power}")

    def stop(self) -> None:
        """Stop the gantry motor immediately."""
        log.info("VESCGantry: stop")
        self._send("STOP")
