"""
gripper.py — Gripper driver for the SauceBot.

Delegates motion control to the Arduino via USB serial.
"""

from pi.utils.logger import log
from pi.motion.arduino_controller import ArduinoController

# ─── Motion constants ──────────────────────────────────────────────────────────
_TICKS_PER_REV         = 753
_CLOSE_REVOLUTIONS     = 2.5
_CLOSE_TARGET_TICKS    = -int(_CLOSE_REVOLUTIONS * _TICKS_PER_REV)   # -1506


class GPIOGripper:
    """
    Controls the 5000 Series 12VDC gripper motor via the ArduinoController.
    """

    def __init__(self):
        self.arduino = ArduinoController()
        log.info("GPIOGripper: Initializing via Arduino USB...")
        self.home()

    def cleanup(self) -> None:
        """Cleanup logic (now handled on Arduino)."""
        log.info("GPIOGripper: cleanup done")

    # ─── Motion ───────────────────────────────────────────────────────────────

    def home(self) -> None:
        """
        Sends HOME_GRIPPER command to Arduino.
        """
        log.info("GPIOGripper: homing...")
        if not self.arduino.send_command("HOME_GRIPPER", timeout=20.0):
            raise RuntimeError("GPIOGripper: homing timed out or failed")
        log.info("GPIOGripper: homing complete")

    def open(self) -> None:
        """Sends MOVE_GRIPPER:0 command to open the gripper."""
        log.info("GPIOGripper: opening...")
        if not self.arduino.send_command("MOVE_GRIPPER:0", timeout=10.0):
            raise RuntimeError("GPIOGripper: open timed out or failed")
        log.info("GPIOGripper: open done")

    def close(self) -> None:
        """Sends MOVE_GRIPPER command to close."""
        log.info("GPIOGripper: closing to target ticks...")
        if not self.arduino.send_command(f"MOVE_GRIPPER:{_CLOSE_TARGET_TICKS}", timeout=15.0):
            raise RuntimeError("GPIOGripper: close timed out or failed")
        log.info("GPIOGripper: close done")
