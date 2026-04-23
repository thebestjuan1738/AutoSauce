"""
gripper.py — Gripper driver for the SauceBot.

Communicates with PrintheadCode.ino on the Arduino Mega via USB serial.
The gripper grabs and releases the sauce bottle.

Commands sent to Arduino:
    HOMEGRAB    -> HOMEGRAB_DONE   (home gripper to open position)
    GRAB        -> GRAB_DONE       (close gripper to grab bottle)
    RELEASE     -> RELEASE_DONE    (open gripper to release bottle)
"""

from pi.utils.logger import log
from pi.motion.arduino_controller import ArduinoController


class GPIOGripper:
    """
    Controls the goBILDA 5000 Series gripper motor via ArduinoController.
    Communicates with PrintheadCode.ino firmware.
    """

    def __init__(self):
        self.arduino = ArduinoController()
        log.info("GPIOGripper: initialized (Arduino already homed at boot)")

    def cleanup(self) -> None:
        """Cleanup logic (handled on Arduino)."""
        log.info("GPIOGripper: cleanup done")

    # ─── Motion ───────────────────────────────────────────────────────────────

    def home(self) -> None:
        """
        Home the gripper to its open position.
        Sends HOMEGRAB, waits for HOMEGRAB_DONE.
        """
        log.info("GPIOGripper: homing...")
        if not self.arduino.send_command("HOMEGRAB", timeout=20.0, done_marker="HOMEGRAB_DONE"):
            raise RuntimeError("GPIOGripper: homing timed out or failed")
        log.info("GPIOGripper: homing complete")

    def close(self) -> None:
        """
        Close the gripper to grab the sauce bottle.
        Sends GRAB, waits for GRAB_DONE.
        """
        log.info("GPIOGripper: closing (grabbing bottle)...")
        if not self.arduino.send_command("GRAB", timeout=15.0, done_marker="GRAB_DONE"):
            raise RuntimeError("GPIOGripper: close/grab timed out or failed")
        log.info("GPIOGripper: grab complete")

    def open(self) -> None:
        """
        Open the gripper to release the sauce bottle.
        Sends RELEASE, waits for RELEASE_DONE.
        """
        log.info("GPIOGripper: opening (releasing bottle)...")
        if not self.arduino.send_command("RELEASE", timeout=15.0, done_marker="RELEASE_DONE"):
            raise RuntimeError("GPIOGripper: open/release timed out or failed")
        log.info("GPIOGripper: release complete")
