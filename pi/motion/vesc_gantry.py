"""
vesc_gantry.py

Standalone serial driver for the gantry HiLetGo NodeMCU ESP8266 controller.
The USB cable from the computer plugs directly into the HiLetGo — no Arduino
board is involved. The firmware was compiled with the Arduino IDE.

Mirrors every command and constant in the gantry C firmware:
  GOTO, FWD, REV, SPEED, SPEEDON, SPEEDOFF, SKP, MSP, CLAMP,
  LOG, STOP, ZERO, POS, DIAG, HELP, KP, KI, KD.

Implements the move_to(position_mm) interface expected by OrderManager.

Wire protocol uses inches; this module converts transparently (1 in = 25.4 mm).

USB port is auto-detected by probing CH340/FTDI/CP210x candidates for a [POS]
response. Falls back to GANTRY_PORT if auto-detection fails.
"""

import sys
import time
import serial
import serial.tools.list_ports

from pi.utils.logger import log

# ─── Constants mirrored from firmware ────────────────────────────────────────

# Serial
GANTRY_PORT = "COM3" if sys.platform == "win32" else "/dev/ttyGANTRY"
GANTRY_BAUD = 115200

# ESC pulse widths (µs)
ESC_MIN  = 1000
ESC_STOP = 1500
ESC_MAX  = 2000

# Travel limits
COUNTS_PER_INCH   = 2053.67
MAX_TRAVEL_INCHES = 13.5
MAX_TRAVEL_COUNTS = int(MAX_TRAVEL_INCHES * COUNTS_PER_INCH)  # 27722

# PID defaults
KP_DEFAULT = 0.15
KI_DEFAULT = 0.0
KD_DEFAULT = 0.10
PID_CLAMP  = 400
DEADBAND   = 15

# Speed control defaults
TARGET_SPEED_IPS_DEFAULT = 2.0
SPEED_KP_DEFAULT         = 2.0
MAX_SPEED_ESTIMATE       = 8.0
MIN_PULSE_OFFSET         = 30
RAMP_DISTANCE_INCHES     = 2.0
MAX_SPEED_HARD_CAP       = 999.0  # no firmware cap — set arbitrarily high

# Homing
HOME_REV_START      = 20
HOME_REV_MAX        = 50
HOME_FWD_INCHES     = 1.0
HOME_REV_MIN        = 10
HOME_REV_MAX_SLOW   = 15
HOME_MOTION_TIMEOUT = 2000   # ms

# ─── Python-side timeouts / convenience ──────────────────────────────────────

# Raise TimeoutError if a move doesn't complete within this many seconds.
TRAVEL_TIMEOUT_S = 30

# Time to wait for the firmware boot sequence (LED test + 5 s encoder test + ESC arming).
BOOT_TIMEOUT_S = 20.0

# Normal point-to-point move speed (inches per second).
TRAVEL_SPEED_IPS = 4.0


# Slow speed used during the sauce dispense sweep (inches per second).
# Note: 0.5 in/s didn't have enough motor torque to maintain constant speed.
SWEEP_SPEED_IPS = 5.0

# Sentinel value imported by order_manager.py — pass as max_duty=SWEEP_MAX_DUTY
# to move_to() to select SWEEP_SPEED_IPS instead of TRAVEL_SPEED_IPS.
SWEEP_MAX_DUTY = 0.51   # kept for backward compatibility with order_manager

# ─── USB port detection ───────────────────────────────────────────────────────

# USB VID of the VESC — always skipped when scanning for the gantry controller.
_VESC_VID = 0x0483   # STMicroelectronics

# Exact VID+PID of the gantry controller — Silicon Labs CP210x UART Bridge.
# Confirmed from `lsusb`: Bus 003 Device 003: ID 10c4:ea60
_GANTRY_VID = 0x10C4
_GANTRY_PID = 0xEA60

# Known non-gantry devices — never probed as gantry candidates.
#   2a03:0042  Arduino Mega 2560 Rev3  → gripper / extruder (ArduinoController)
#   2341:0043  Arduino Uno R3          → conveyor belt (GPIOConveyor)
_SKIP_DEVICES = frozenset({
    (0x2A03, 0x0042),
    (0x2341, 0x0043),
})

# VIDs and description substrings associated with NodeMCU / HiLetGo clones.
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
         No POS probe — the firmware may still be in its boot sequence and
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
    Standalone serial driver for the gantry HiLetGo NodeMCU ESP8266 controller.

    The firmware handles closed-loop position control using an encoder and ESC.
    Python sends plain ASCII commands and reads tagged response lines over USB.

    Mirrors all firmware commands:
      GOTO, FWD, REV, SPEED, SPEEDON, SPEEDOFF, SKP, MSP, CLAMP,
      LOG, STOP, ZERO, POS, DIAG, HELP, KP, KI, KD.

    Unit conversion: mm (Python API) <-> inches (firmware wire protocol).
    move_to() is the primary interface used by OrderManager.
    """

    def __init__(self):
        port = _find_gantry_port()
        log.info("VESCGantry: connecting to %s @ %d baud", port, GANTRY_BAUD)
        self._ser = serial.Serial(port, GANTRY_BAUD, timeout=0.5)
        self._position_mm = 0
        log.info("VESCGantry: connected")
        # DTR pulse resets the ESP8266 so setup() always runs fresh and re-arms the ESC.
        log.info("VESCGantry: resetting ESP8266 via DTR pulse...")
        self._ser.setDTR(False)
        time.sleep(0.1)
        self._ser.setDTR(True)
        self._ser.reset_input_buffer()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _send(self, cmd: str) -> None:
        """Write a newline-terminated command to the firmware."""
        self._ser.write(f"{cmd}\n".encode('utf-8'))
        self._ser.flush()
        log.debug("VESCGantry → %s", cmd)

    def _readline(self) -> str:
        """Read one line from the firmware. Returns '' on timeout (0.5 s per call)."""
        try:
            return self._ser.readline().decode('utf-8', errors='ignore').strip()
        except Exception:
            return ''

    # ── Boot ──────────────────────────────────────────────────────────────────

    def boot_check(self) -> None:
        """
        Wait for the firmware boot sequence to finish, then verify with POS.

        Two cases:
          - Fresh boot: banner takes ~10 s (LED test, encoder test, ESC arming).
            Detected by the "Counts/inch" + "---" separator at the end of the banner.
          - Already running: [STATUS] lines arrive immediately within 2 s.

        The ESP8266 boot ROM outputs at 74880 baud before the app starts at 115200,
        so the first several seconds of output are garbled. [STATUS] lines begin
        arriving ~10 s in but may themselves be partially corrupted. Boot detection
        therefore uses partial-match fallbacks in addition to exact strings.

        Raises RuntimeError if firmware doesn't respond within BOOT_TIMEOUT_S.
        """
        log.info("VESCGantry: waiting for firmware boot...")
        self._ser.reset_input_buffer()

        # Quick check: already running? (firmware was live before DTR reset or
        # boot_check called without a reset — STATUS arrives within 2 s).
        quick_deadline = time.monotonic() + 2.0
        while time.monotonic() < quick_deadline:
            line = self._readline()
            if not line:
                continue
            log.debug("VESCGantry boot: %s", line)
            if self._is_status_line(line):
                log.info("VESCGantry: firmware already running — skipping banner wait")
                break
        else:
            # Fresh boot path: wait for banner completion marker ("Counts/inch" then "---").
            # Also accept STATUS-like lines as confirmation the banner is done —
            # they start appearing ~10 s in even when partially garbled.
            counts_seen = False
            deadline = time.monotonic() + BOOT_TIMEOUT_S
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
                elif self._is_status_line(line):
                    log.info("VESCGantry: STATUS detected during banner — firmware running")
                    break

        # Verify two-way comms with POS.  Retry up to 3 times (9 s total) because
        # STATUS lines flooding the buffer can delay or garble the first POS response.
        for attempt in range(1, 4):
            self._ser.reset_input_buffer()
            self._send("POS")
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                line = self._readline()
                if '[POS]' in line:
                    log.info("VESCGantry: boot check passed — %s", line)
                    # Auto-home the gantry after successful boot check
                    log.info("VESCGantry: auto-homing on startup...")
                    try:
                        self.home()
                    except RuntimeError as e:
                        log.warning("VESCGantry: auto-home failed (%s) — continuing without homing", e)
                        log.warning("VESCGantry: gantry position may be unknown until manually homed")
                    return
                if '[ERR]' in line:
                    raise RuntimeError(f"VESCGantry boot check error: {line}")
            log.warning("VESCGantry: POS attempt %d/3 timed out — retrying", attempt)

        raise RuntimeError("VESCGantry: did not respond to POS command during boot check")

    @staticmethod
    def _is_status_line(line: str) -> bool:
        """
        Return True if line looks like a firmware [STATUS] line.
        Accepts partial/garbled variants produced by the ESP8266 boot ROM baud-rate
        mismatch (e.g. '[STATS]', '[STTUS', '[TUS]') by checking for substrings
        that reliably survive mild corruption: 'Pos:' + 'in/s', or 'FWD:' + 'ESC:'.
        """
        if '[STATUS]' in line:
            return True
        # Partial tag patterns seen in garbled output
        if any(tag in line for tag in ('[STATS]', '[STTUS', '[STATUS', 'STATUS]')):
            return True
        # Content patterns that only appear in STATUS lines
        if 'Pos:' in line and 'in/s' in line:
            return True
        if 'FWD:' in line and 'ESC:' in line:
            return True
        return False

    # ── Position & status ─────────────────────────────────────────────────────

    def get_position_mm(self) -> float:
        """
        Query firmware for current encoder position and return it in mm.
        Parses: [POS] X.XXX in (counts) | Speed: ...
        """
        self._ser.reset_input_buffer()
        self._send("POS")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            line = self._readline()
            if '[POS]' in line:
                try:
                    pos_in = float(line.split()[1])
                    return pos_in * 25.4
                except (IndexError, ValueError):
                    pass
        log.warning("VESCGantry: could not read position, returning cached value")
        return float(self._position_mm)

    def diag(self) -> str:
        """
        Send DIAG command. Collects and returns the full diagnostics block.
        Mirrors the DIAG / D command in the firmware.
        """
        self._ser.reset_input_buffer()
        self._send("DIAG")
        lines = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            line = self._readline()
            if not line:
                continue
            lines.append(line)
            if line.startswith('---') and len(lines) > 2:
                break
        result = '\n'.join(lines)
        log.debug("VESCGantry DIAG:\n%s", result)
        return result

    def help(self) -> str:
        """
        Send HELP command. Collects and returns the help text block.
        Mirrors the HELP / H command in the firmware.
        """
        self._ser.reset_input_buffer()
        self._send("HELP")
        lines = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            line = self._readline()
            if not line:
                continue
            lines.append(line)
            if line.startswith('---') and len(lines) > 2:
                break
        return '\n'.join(lines)

    # ── Motion ────────────────────────────────────────────────────────────────

    def goto(self, inches: float) -> None:
        """
        Send GOTO<inches> directly in inches. Non-blocking — firmware runs
        the closed-loop move. Raises ValueError if out of range.
        Use move_to() for the blocking mm-based OrderManager interface.
        """
        if inches < 0.0 or inches > MAX_TRAVEL_INCHES:
            raise ValueError(
                f"GOTO out of range: {inches:.4f} in (valid 0.0–{MAX_TRAVEL_INCHES})"
            )
        self._send(f"GOTO{inches:.4f}")
        log.info("VESCGantry: GOTO%.4f in", inches)

    def fwd(self, power: int) -> None:
        """
        Drive gantry forward (away from home). power: 0–100.
        Mirrors FWD command. Firmware applies the hard speed cap internally.
        """
        power = max(0, min(100, power))
        self._send(f"FWD{power}")
        log.info("VESCGantry: FWD%d", power)

    def rev(self, power: int) -> None:
        """
        Drive gantry reverse (toward home). power: 0–100.
        Mirrors REV command. Firmware applies the hard speed cap internally.
        """
        power = max(0, min(100, power))
        self._send(f"REV{power}")
        log.info("VESCGantry: REV%d", power)

    def stop(self) -> None:
        """
        Send STOP — halts motor immediately, clears movingToTarget and
        homingActive flags in firmware. Mirrors STOP / S command.
        """
        self._send("STOP")
        log.info("VESCGantry: STOP")

    def home(self) -> None:
        """
        Run homing routine (ZERO command). Reverses to the limit switch,
        zeros the encoder, then confirms exact zero via slow creep.
        Blocks until "[ZERO] Ready." Raises RuntimeError on failure or
        timeout (60 s). Mirrors ZERO command.
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
            if "HOMING COMPLETE" in line or "[ZERO] Ready." in line:
                self._position_mm = 0
                log.info("VESCGantry: homing complete, position = 0 mm")
                return
            if "[ZERO]" in line and "ERROR" in line:
                raise RuntimeError(f"VESCGantry homing failed: {line}")
        raise RuntimeError("VESCGantry: homing timed out after 60 s")

    # ── Speed control ─────────────────────────────────────────────────────────

    def set_speed(self, ips: float) -> None:
        """
        Set max move speed in inches/sec. Valid: 0.1 – MAX_SPEED_HARD_CAP (6.0).
        Mirrors MSPD<in/s> command.
        """
        if ips <= 0.0 or ips > MAX_SPEED_HARD_CAP:
            raise ValueError(
                f"Speed out of range: {ips} in/s (valid 0.1–{MAX_SPEED_HARD_CAP})"
            )
        self._send(f"MSPD{ips:.2f}")
        log.info("VESCGantry: MSPD%.2f in/s", ips)

    def speed_on(self) -> None:
        """
        Enable feedforward speed control and clear the integral.
        Mirrors SPEEDON command.
        """
        self._send("SPEEDON")
        log.info("VESCGantry: SPEEDON")

    def speed_off(self) -> None:
        """
        Disable speed control — use raw PID instead. Clears integral.
        Mirrors SPEEDOFF command.
        """
        self._send("SPEEDOFF")
        log.info("VESCGantry: SPEEDOFF")

    def set_speed_kp(self, kp: float) -> None:
        """
        Set the speed feedback proportional gain.
        Mirrors SKP<val> command.
        """
        self._send(f"SKP{kp}")
        log.info("VESCGantry: SKP%s", kp)

    def set_max_speed_estimate(self, msp: float) -> None:
        """
        Set the maximum speed estimate for feedforward scaling.
        Mirrors MSP<val> command.
        """
        self._send(f"MSP{msp}")
        log.info("VESCGantry: MSP%s", msp)

    def set_clamp(self, clamp: int) -> None:
        """
        Set the PID/speed output clamp. Firmware constrains to 50–500.
        Mirrors CLAMP<val> command.
        """
        self._send(f"CLAMP{clamp}")
        log.info("VESCGantry: CLAMP%d", clamp)

    # ── PID tuning ────────────────────────────────────────────────────────────

    def set_kp(self, kp: float) -> None:
        """Set raw PID proportional gain. Mirrors KP<val> command."""
        self._send(f"KP{kp}")
        log.info("VESCGantry: KP%s", kp)

    def set_ki(self, ki: float) -> None:
        """Set raw PID integral gain. Mirrors KI<val> command."""
        self._send(f"KI{ki}")
        log.info("VESCGantry: KI%s", ki)

    def set_kd(self, kd: float) -> None:
        """Set raw PID derivative gain. Mirrors KD<val> command."""
        self._send(f"KD{kd}")
        log.info("VESCGantry: KD%s", kd)

    # ── Logging ───────────────────────────────────────────────────────────────

    def toggle_log(self) -> None:
        """
        Toggle live CSV data stream on the firmware.
        When ON the firmware streams: ms,pos_in,target_in,error_in,speed_in_s,esc_us
        Mirrors LOG command.
        """
        self._send("LOG")
        log.info("VESCGantry: LOG toggled")

    def read_log_line(self) -> str:
        """
        Read a single line from the firmware (non-blocking, returns '' on timeout).
        Use to consume live LOG data or any unsolicited firmware output.
        """
        return self._readline()

    # ── High-level OrderManager interface ─────────────────────────────────────

    def move_to(self, position_mm: int, max_duty: float = 1.0, speed_ips: float = None) -> None:
        """
        Blocking closed-loop move to position_mm from the dock end of the rail.
        Sets speed (MSPD), then sends GOTO. Waits for "[GOTO] Arrived at".

        Pass speed_ips to override the default travel speed (e.g. for dispense sweep).

        Raises TimeoutError if the move doesn't complete within TRAVEL_TIMEOUT_S.
        Raises RuntimeError on firmware-reported errors or limit conditions.
        """
        if position_mm == self._position_mm:
            return

        if speed_ips is None:
            speed_ips = SWEEP_SPEED_IPS if max_duty <= SWEEP_MAX_DUTY else TRAVEL_SPEED_IPS
        position_in = position_mm / 25.4

        log.info(
            "VESCGantry: move_to %d mm (%.4f in) at %.2f in/s",
            position_mm, position_in, speed_ips,
        )

        self._ser.reset_input_buffer()
        # Set max move speed then flush ACK before issuing GOTO
        self._send(f"MSPD{speed_ips:.2f}")
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

    # ── Backward-compat aliases ───────────────────────────────────────────────

    def start(self, speed: int) -> None:
        """Alias for fwd(). Drive gantry forward. speed: 0–100."""
        self.fwd(speed)

    def reverse(self, speed: int) -> None:
        """Alias for rev(). Drive gantry backward. speed: 0–100."""
        self.rev(speed)
