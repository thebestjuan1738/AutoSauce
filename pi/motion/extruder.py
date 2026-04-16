"""
extruder.py — Extruder driver for the SauceBot.

Delegates motion control to the Arduino via USB serial.
"""

from pi.utils.logger import log
from pi.motion.arduino_controller import ArduinoController


class GPIOExtruder:
    """
    Controls the 5000 Series 12VDC extruder via the ArduinoController.
    """

    def __init__(self):
        self.arduino = ArduinoController()
        log.info("GPIOExtruder: Initializing via Arduino USB...")
        self.home()

    def cleanup(self) -> None:
        """Cleanup logic (now handled on Arduino)."""
        log.info("GPIOExtruder: cleanup done")

    # ─── Motion ───────────────────────────────────────────────────────────────

    def home(self) -> None:
        """
        Sends HOME_EXTRUDER command to Arduino.
        """
        log.info("GPIOExtruder: homing...")
        if not self.arduino.send_command("HOME_EXTRUDER", timeout=20.0):
            raise RuntimeError("GPIOExtruder: homing timed out or failed")
        log.info("GPIOExtruder: homing complete")

    def dispense(self) -> None:
        """
        Sends MEET_PLUNGER command to Arduino.
        Phase 1: drives until the contact pad is touched.
        Phase 2: advances TICKS_PAST_CONTACT ticks past contact.
        """
        log.info("GPIOExtruder: dispensing via collision detection (MEET_PLUNGER)...")
        if not self.arduino.send_command("MEET_PLUNGER", timeout=45.0):
            raise RuntimeError("GPIOExtruder: dispense timed out or failed")
        log.info("GPIOExtruder: dispense done")

    def retract(self) -> None:
        """Sends MOVE_EXTRUDER:0 to retract plunger to home."""
        log.info("GPIOExtruder: retracting...")
        if not self.arduino.send_command("MOVE_EXTRUDER:0", timeout=45.0):
            raise RuntimeError("GPIOExtruder: retract timed out or failed")
        log.info("GPIOExtruder: retract done")
