"""
mock_drivers.py

Fake hardware drivers for development and testing.
Pass these into OrderManager instead of real GPIO drivers
when running on a laptop or before hardware is wired up.

Usage in main.py:
    from pi.motion.mock_drivers import MockGantry, MockGripper, MockExtruder, MockConveyor
    order_manager = OrderManager(
        gantry=MockGantry(),
        gripper=MockGripper(),
        extruder=MockExtruder(),
        conveyor=MockConveyor(),
    )
"""

import time
from pi.utils.logger import log


class MockGantry:
    def __init__(self):
        self._position = 500   # starts at home

    def move_to(self, position_mm: int) -> None:
        log.info(f"  [MOCK] Gantry: {self._position}mm → {position_mm}mm")
        time.sleep(0.2)        # simulate travel time
        self._position = position_mm


class MockGripper:
    def home(self) -> None:
        log.info("  [MOCK] Gripper: homing")

    def close(self) -> None:
        log.info("  [MOCK] Gripper: closing")

    def open(self) -> None:
        log.info("  [MOCK] Gripper: opening")


class MockExtruder:
    def home(self) -> None:
        log.info("  [MOCK] Extruder: homing")

    def dispense(self) -> None:
        log.info("  [MOCK] Extruder: dispensing")

    def retract(self) -> None:
        log.info("  [MOCK] Extruder: retracting")


class MockConveyor:
    def start(self, speed: int) -> None:
        log.info(f"  [MOCK] Conveyor: starting at speed {speed}")

    def stop(self) -> None:
        log.info("  [MOCK] Conveyor: stopped")
