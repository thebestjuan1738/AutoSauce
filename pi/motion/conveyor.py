"""
conveyor.py — Conveyor belt driver for the SauceBot.

Hardware:
    Arduino Uno R3 (VID=0x2341, PID=0x0043) controlling a relay-switched DC motor.
    Firmware: FIXEDSPEEDPOSITIONCONTROLCONVEYORBELT.ino  (baud 9600)

The Arduino drives the belt at a fixed relay speed — there is no speed control.
The speed argument accepted by start() and reverse() is kept for interface
compatibility with OrderManager but is ignored.

start(speed)   — sends FWD<large distance> so the belt runs until stop() is called.
reverse(speed) — stops then sends REV<large distance>.
stop()         — sends STOP immediately.
"""

import sys
import time
import serial
import serial.tools.list_ports

from pi.utils.logger import log

# ─── Config ───────────────────────────────────────────────────────────────────

# Fallback port if VID/PID detection fails.
CONVEYOR_PORT = "COM5" if sys.platform == "win32" else "/dev/ttyACM0"
CONVEYOR_BAUD = 9600

# Time to wait for the Arduino boot banner on first connect.
BOOT_TIMEOUT_S = 5.0

# Distance (mm) sent for a "run until stopped" command.
# Arduino int is 16-bit on Uno (max 32767) — 30000 mm ≈ 30 m, never reached in practice.
_BIG_DIST = 30000

# USB VID+PID of the Arduino Uno R3, confirmed from lsusb:
#   Bus 003 Device 005: ID 2341:0043 Arduino SA Uno R3 (CDC ACM)
_CONVEYOR_VID = 0x2341
_CONVEYOR_PID = 0x0043


# ─── Port detection ───────────────────────────────────────────────────────────

def _find_conveyor_port() -> str:
    """
    Find the conveyor Arduino Uno by exact VID+PID (0x2341:0x0043).
    Falls back to CONVEYOR_PORT if not found.
    """
    for p in serial.tools.list_ports.comports():
        if p.vid == _CONVEYOR_VID and p.pid == _CONVEYOR_PID:
            log.info("ArduinoConveyor: found Uno on %s (%s)", p.device, p.description)
            return p.device
    log.warning("ArduinoConveyor: Uno (2341:0043) not found — falling back to %s", CONVEYOR_PORT)
    return CONVEYOR_PORT


# ─── Driver ───────────────────────────────────────────────────────────────────

class GPIOConveyor:
    """
    Controls the conveyor belt via an Arduino Uno R3 over USB serial.

    The Arduino firmware uses relay outputs at a fixed speed; this driver
    has no speed control. The speed argument is accepted for interface
    compatibility with OrderManager but is ignored.
    """

    def __init__(self):
        port = _find_conveyor_port()
        log.info("ArduinoConveyor: connecting to %s @ %d baud", port, CONVEYOR_BAUD)
        self._ser = serial.Serial(port, CONVEYOR_BAUD, timeout=1)
        self._wait_for_ready()
        log.info("ArduinoConveyor: ready")

    def _wait_for_ready(self) -> None:
        """Wait for the Arduino boot banner. Continues silently if already running."""
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._ser.in_waiting:
                line = self._ser.readline().decode('utf-8', errors='ignore').strip()
                log.debug("ArduinoConveyor boot: %s", line)
                if line.startswith('Ready'):
                    return
        log.info("ArduinoConveyor: no boot banner received (already running?), continuing")

    def _send(self, cmd: str) -> None:
        self._ser.write(f"{cmd}\n".encode('utf-8'))
        self._ser.flush()
        log.debug("ArduinoConveyor → %s", cmd)

    def start(self, speed: int) -> None:
        """
        Run the conveyor belt forward.
        speed is accepted for interface compatibility but ignored — the relay
        hardware runs at a single fixed speed.
        """
        log.info("ArduinoConveyor: forward (relay — speed arg ignored)")
        self._send(f"FWD{_BIG_DIST}")

    def reverse(self, speed: int) -> None:
        """
        Run the conveyor belt backward.
        Sends STOP first since the firmware rejects REV while moving.
        """
        log.info("ArduinoConveyor: reverse (relay — speed arg ignored)")
        self._send("STOP")
        time.sleep(0.15)
        self._send(f"REV{_BIG_DIST}")

    def stop(self) -> None:
        """Stop the conveyor belt immediately."""
        log.info("ArduinoConveyor: stop")
        self._send("STOP")
