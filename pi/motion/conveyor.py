"""
conveyor.py — Conveyor belt driver for the SauceBot.

Communicates with ConveyorHotdogCode.ino on an Arduino via USB serial.
Controls the conveyor belt, cylinder gripper, and heat lamp.

Hardware:
    Arduino controlling conveyor with encoder + relays, plus a rotary cylinder.
    Firmware: ConveyorHotdogCode.ino (baud 9600)

Station positions (mm from home):
    HOTDOG = 268mm, HEAT = 438mm, SAUCE = 739mm, PICKUP = 1020mm

Commands sent to Arduino:
    HOME         -> HOME_DONE           (zero position)
    HOTDOG       -> MOVE_DONE:HOTDOG    (move to hotdog station)
    HEAT         -> MOVE_DONE:HEAT      (move to heat station)
    SAUCE        -> MOVE_DONE:SAUCE     (move to sauce station)
    PICKUP       -> MOVE_DONE:PICKUP    (move to pickup station)
    FWD<mm>      -> MOVE_DONE:FWD       (move forward by mm)
    REV<mm>      -> MOVE_DONE:REV       (move backward by mm)
    ZIGZAG       -> (starts zigzag mode, send ZIGZAGSTOP to stop)
    ZIGZAGSTOP   -> MOVE_DONE:ZIGZAG    (stop zigzag and return to center)
    CONVSTOP     -> CONV_STOPPED        (emergency stop)
    GRAB         -> CYL_DONE:GRAB       (cylinder rotate to grab position 274°)
    DROP         -> CYL_DONE:DROP       (cylinder rotate to drop position 197°)
    LAMPON       -> LAMP_DONE:ON        (turn on heat lamp)
    LAMPOFF      -> LAMP_DONE:OFF       (turn off heat lamp)
    STATUS       -> (prints status, no done marker)
"""

import sys
import time
import serial
import serial.tools.list_ports
import threading

from pi.utils.logger import log

# ─── Config ───────────────────────────────────────────────────────────────────

# Fallback port if VID/PID detection fails.
CONVEYOR_PORT = "COM5" if sys.platform == "win32" else "/dev/ttyCONVEYOR"
CONVEYOR_BAUD = 9600

# Time to wait for the Arduino boot banner on first connect.
BOOT_TIMEOUT_S = 5.0

# Move timeout (max time for any single move command).
MOVE_TIMEOUT_S = 60.0

# USB VID+PID of the Arduino Uno R3:
#   Bus 003 Device 005: ID 2341:0043 Arduino SA Uno R3 (CDC ACM)
_CONVEYOR_VID = 0x2341
_CONVEYOR_PID = 0x0043


# ─── Port detection ───────────────────────────────────────────────────────────

def _find_conveyor_port() -> str:
    """
    Find the conveyor Arduino by exact VID+PID (0x2341:0x0043).
    Falls back to CONVEYOR_PORT if not found.
    """
    for p in serial.tools.list_ports.comports():
        if p.vid == _CONVEYOR_VID and p.pid == _CONVEYOR_PID:
            log.info("ConveyorController: found Arduino on %s (%s)", p.device, p.description)
            return p.device
    log.warning("ConveyorController: Arduino (2341:0043) not found — falling back to %s", CONVEYOR_PORT)
    return CONVEYOR_PORT


# ─── Driver ───────────────────────────────────────────────────────────────────

class GPIOConveyor:
    """
    Controls the conveyor belt, cylinder gripper, and heat lamp via Arduino.
    Communicates with ConveyorHotdogCode.ino firmware.
    """

    def __init__(self):
        port = _find_conveyor_port()
        log.info("ConveyorController: connecting to %s @ %d baud", port, CONVEYOR_BAUD)
        self._ser = serial.Serial(port, CONVEYOR_BAUD, timeout=1)
        self._lock = threading.Lock()
        self._wait_for_ready()
        log.info("ConveyorController: ready")

    def _wait_for_ready(self) -> None:
        """Wait for the Arduino boot banner."""
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._ser.in_waiting:
                line = self._ser.readline().decode('utf-8', errors='ignore').strip()
                log.debug("ConveyorController boot: %s", line)
                # The firmware prints "Send HOME to zero position." at startup
                if 'HOME' in line or 'STATUS' in line:
                    return
        log.info("ConveyorController: no boot banner received (already running?), continuing")

    def _reconnect(self) -> None:
        """Reopen the serial port after an EIO / USB re-enumeration."""
        log.warning("ConveyorController: serial error — reconnecting...")
        try:
            self._ser.close()
        except Exception:
            pass
        # Give the Uno time to re-enumerate and boot
        time.sleep(2.0)
        port = _find_conveyor_port()
        self._ser = serial.Serial(port, CONVEYOR_BAUD, timeout=1)
        self._wait_for_ready()
        log.info("ConveyorController: reconnected on %s", port)

    def _send(self, cmd: str) -> None:
        """Send a command to the Arduino, reconnecting on EIO."""
        try:
            self._ser.write(f"{cmd}\n".encode('utf-8'))
            self._ser.flush()
            log.debug("ConveyorController → %s", cmd)
        except (serial.SerialException, OSError) as e:
            log.error("ConveyorController: send error (%s) — reconnecting", e)
            self._reconnect()
            self._ser.write(f"{cmd}\n".encode('utf-8'))
            self._ser.flush()
            log.debug("ConveyorController → %s (after reconnect)", cmd)

    def _wait_for_done(self, done_marker: str, timeout: float = MOVE_TIMEOUT_S) -> bool:
        """
        Wait for a specific done marker from the Arduino.

        Args:
            done_marker: Expected completion string (e.g., "MOVE_DONE:SAUCE")
            timeout: Max seconds to wait

        Returns:
            True if done marker received, False on timeout or error.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self._ser.in_waiting:
                    line = self._ser.readline().decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                    log.info(f"Conveyor: {line}")
                    if line == done_marker:
                        return True
                    # Check for partial match (e.g., "MOVE_DONE:" prefix)
                    if done_marker.startswith("MOVE_DONE:") and line.startswith("MOVE_DONE:"):
                        return True
                    if done_marker.startswith("CYL_DONE:") and line.startswith("CYL_DONE:"):
                        return True
            except (serial.SerialException, OSError) as e:
                log.error("ConveyorController: read error (%s) — reconnecting", e)
                self._reconnect()
            time.sleep(0.01)
        log.error(f"ConveyorController: timeout waiting for {done_marker}")
        return False

    def _send_and_wait(self, cmd: str, done_marker: str, timeout: float = MOVE_TIMEOUT_S) -> bool:
        """Send a command and wait for completion."""
        with self._lock:
            self._ser.reset_input_buffer()
            self._send(cmd)
            return self._wait_for_done(done_marker, timeout)

    # ─── Conveyor Belt Movement ────────────────────────────────────────────────

    def home(self) -> None:
        """
        Zero the conveyor position.
        Sends HOME, waits for HOME_DONE.
        """
        log.info("ConveyorController: homing...")
        if not self._send_and_wait("HOME", "HOME_DONE", timeout=10.0):
            raise RuntimeError("ConveyorController: homing failed")
        log.info("ConveyorController: home complete")

    def move_to_station(self, station: str) -> None:
        """
        Move conveyor to a named station.

        Args:
            station: One of "hotdog", "heat", "sauce", "pickup"
        """
        station = station.upper()
        valid_stations = ["HOTDOG", "HEAT", "SAUCE", "PICKUP"]
        if station not in valid_stations:
            raise ValueError(f"Invalid station '{station}'. Must be one of {valid_stations}")

        log.info(f"ConveyorController: moving to {station} station...")
        if not self._send_and_wait(station, f"MOVE_DONE:{station}"):
            raise RuntimeError(f"ConveyorController: move to {station} failed")
        log.info(f"ConveyorController: arrived at {station}")

    def move_forward(self, distance_mm: int) -> None:
        """
        Move conveyor forward by specified distance.

        Args:
            distance_mm: Distance to move in mm
        """
        log.info(f"ConveyorController: moving forward {distance_mm}mm...")
        if not self._send_and_wait(f"FWD{distance_mm}", "MOVE_DONE:FWD"):
            raise RuntimeError(f"ConveyorController: forward move failed")
        log.info("ConveyorController: forward move complete")

    def move_reverse(self, distance_mm: int) -> None:
        """
        Move conveyor backward by specified distance.

        Args:
            distance_mm: Distance to move in mm
        """
        log.info(f"ConveyorController: moving reverse {distance_mm}mm...")
        if not self._send_and_wait(f"REV{distance_mm}", "MOVE_DONE:REV"):
            raise RuntimeError(f"ConveyorController: reverse move failed")
        log.info("ConveyorController: reverse move complete")

    def start_zigzag(self) -> None:
        """
        Start zigzag oscillation mode (±25mm from current position).
        Use stop_zigzag() to stop and return to center.
        """
        log.info("ConveyorController: starting zigzag...")
        with self._lock:
            self._ser.reset_input_buffer()
            self._send("ZIGZAG")
            # Wait briefly for acknowledgement
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if self._ser.in_waiting:
                    line = self._ser.readline().decode('utf-8', errors='ignore').strip()
                    log.info(f"Conveyor: {line}")
                    if "[ZIGZAG]" in line and "Started" in line:
                        log.info("ConveyorController: zigzag started")
                        return
                time.sleep(0.01)
        log.info("ConveyorController: zigzag command sent")

    def stop_zigzag(self) -> None:
        """
        Stop zigzag mode and return to center position.
        """
        log.info("ConveyorController: stopping zigzag...")
        if not self._send_and_wait("ZIGZAGSTOP", "MOVE_DONE:ZIGZAG", timeout=10.0):
            raise RuntimeError("ConveyorController: zigzag stop failed")
        log.info("ConveyorController: zigzag stopped, returned to center")

    def stop(self) -> None:
        """Emergency stop the conveyor."""
        log.info("ConveyorController: emergency stop")
        with self._lock:
            self._send("CONVSTOP")
            # Don't wait for response on emergency stop

    # ─── Legacy interface for OrderManager compatibility ──────────────────────

    def start(self, speed: int) -> None:
        """
        Start conveyor forward (legacy interface).
        Speed is ignored - hardware runs at fixed speed.
        Runs until stop() is called.
        """
        log.info("ConveyorController: start forward (legacy, speed ignored)")
        with self._lock:
            self._ser.reset_input_buffer()
            self._send("FWD30000")  # Large distance, will be stopped manually

    def reverse(self, speed: int) -> None:
        """
        Start conveyor reverse (legacy interface).
        Speed is ignored - hardware runs at fixed speed.
        """
        log.info("ConveyorController: start reverse (legacy, speed ignored)")
        with self._lock:
            self._send("CONVSTOP")
            time.sleep(0.15)
            self._send("REV30000")

    # ─── Cylinder Gripper ──────────────────────────────────────────────────────

    def cylinder_grab(self) -> None:
        """
        Rotate cylinder to grab position (274°).
        Used to pick up hotdog from conveyor.
        """
        log.info("ConveyorController: cylinder grab...")
        if not self._send_and_wait("GRAB", "CYL_DONE:GRAB", timeout=15.0):
            raise RuntimeError("ConveyorController: cylinder grab failed")
        log.info("ConveyorController: cylinder at grab position")

    def cylinder_drop(self) -> None:
        """
        Rotate cylinder to drop position (197°).
        Used to release hotdog.
        """
        log.info("ConveyorController: cylinder drop...")
        if not self._send_and_wait("DROP", "CYL_DONE:DROP", timeout=15.0):
            raise RuntimeError("ConveyorController: cylinder drop failed")
        log.info("ConveyorController: cylinder at drop position")

    # ─── Heat Lamp ─────────────────────────────────────────────────────────────

    def lamp_on(self) -> None:
        """Turn on the heat lamp."""
        log.info("ConveyorController: lamp on...")
        if not self._send_and_wait("LAMPON", "LAMP_DONE:ON", timeout=5.0):
            raise RuntimeError("ConveyorController: lamp on failed")
        log.info("ConveyorController: lamp is on")

    def lamp_off(self) -> None:
        """Turn off the heat lamp."""
        log.info("ConveyorController: lamp off...")
        if not self._send_and_wait("LAMPOFF", "LAMP_DONE:OFF", timeout=5.0):
            raise RuntimeError("ConveyorController: lamp off failed")
        log.info("ConveyorController: lamp is off")
