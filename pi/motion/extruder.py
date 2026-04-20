"""
extruder.py — Extruder driver for the SauceBot.

Communicates with PrintheadCode.ino on the Arduino Mega via USB serial.
The extruder pushes the plunger to dispense sauce.

Commands sent to Arduino:
    HOMEEXT      -> HOMEEXT_DONE   (home extruder to retracted position)
    MEETPLUNGER  -> PLUNGER_DONE   (drive until plunger contact detected)
    EXTRUDESLOW  -> EXTRUDING      (start dispensing at slow speed, PWM 1430)
    EXTRUDEMED   -> EXTRUDING      (start dispensing at medium speed, PWM 1400)
    EXTRUDEFAST  -> EXTRUDING      (start dispensing at fast speed, PWM 1370)
    STOPEXT      -> STOPEXT_DONE   (stop extruder motor)
    OPENEXT      -> OPENEXT_DONE   (retract extruder to home position)
"""

from pi.utils.logger import log
from pi.motion.arduino_controller import ArduinoController


class GPIOExtruder:
    """
    Controls the goBILDA 5000 Series extruder motor via ArduinoController.
    Communicates with PrintheadCode.ino firmware.
    """

    def __init__(self):
        self.arduino = ArduinoController()
        log.info("GPIOExtruder: Initializing via Arduino USB...")
        self.home()

    def cleanup(self) -> None:
        """Cleanup logic (handled on Arduino)."""
        log.info("GPIOExtruder: cleanup done")

    # ─── Motion ───────────────────────────────────────────────────────────────

    def home(self) -> None:
        """
        Home the extruder to its retracted position.
        Sends HOMEEXT, waits for HOMEEXT_DONE.
        """
        log.info("GPIOExtruder: homing...")
        if not self.arduino.send_command("HOMEEXT", timeout=20.0, done_marker="HOMEEXT_DONE"):
            raise RuntimeError("GPIOExtruder: homing timed out or failed")
        log.info("GPIOExtruder: homing complete")

    def meet_plunger(self) -> None:
        """
        Drive extruder forward until the plunger contact sensor is triggered.
        Blocks until Arduino confirms contact (PLUNGER_DONE).
        Call this before dispense() to confirm contact before gantry sweep.
        """
        log.info("GPIOExtruder: meeting plunger...")
        if not self.arduino.send_command("MEETPLUNGER", timeout=45.0, done_marker="PLUNGER_DONE"):
            raise RuntimeError("GPIOExtruder: MEETPLUNGER timed out or failed")
        log.info("GPIOExtruder: plunger contact confirmed")

    def dispense(self, speed: str = "medium") -> None:
        """
        Start dispensing sauce at the specified speed.
        This is a non-blocking command - the extruder will run continuously
        until stop_dispense() is called.

        Args:
            speed: "slow", "medium", or "fast"
        """
        speed_cmds = {
            "slow": "EXTRUDESLOW",
            "medium": "EXTRUDEMED",
            "fast": "EXTRUDEFAST",
        }
        cmd = speed_cmds.get(speed.lower(), "EXTRUDEMED")
        log.info(f"GPIOExtruder: starting dispense ({speed})...")

        # Send command and wait for EXTRUDING acknowledgement
        if not self.arduino.send_command(cmd, timeout=5.0, done_marker="EXTRUDING"):
            raise RuntimeError(f"GPIOExtruder: {cmd} failed to start")
        log.info("GPIOExtruder: dispensing...")

    def stop_dispense(self) -> None:
        """
        Stop the extruder motor (stops dispensing).
        Sends STOPEXT, waits for STOPEXT_DONE.
        """
        log.info("GPIOExtruder: stopping dispense...")
        if not self.arduino.send_command("STOPEXT", timeout=5.0, done_marker="STOPEXT_DONE"):
            raise RuntimeError("GPIOExtruder: STOPEXT timed out or failed")
        log.info("GPIOExtruder: dispense stopped")

    def retract(self) -> None:
        """
        Retract the extruder to its home position.
        Sends OPENEXT, waits for OPENEXT_DONE.
        """
        log.info("GPIOExtruder: retracting...")
        if not self.arduino.send_command("OPENEXT", timeout=45.0, done_marker="OPENEXT_DONE"):
            raise RuntimeError("GPIOExtruder: retract timed out or failed")
        log.info("GPIOExtruder: retract complete")
