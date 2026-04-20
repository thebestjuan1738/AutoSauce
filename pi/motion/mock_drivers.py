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
    """Mock gantry driver for testing without hardware."""

    def __init__(self):
        self._position = 0   # starts at home

    def move_to(self, position_mm: int, max_duty: float = 1.0) -> None:
        log.info(f"  [MOCK] Gantry: {self._position}mm → {position_mm}mm (duty={max_duty})")
        time.sleep(0.2)        # simulate travel time
        self._position = position_mm

    def home(self) -> None:
        log.info("  [MOCK] Gantry: homing")
        time.sleep(0.5)
        self._position = 0
        log.info("  [MOCK] Gantry: homed at 0mm")

    def get_position_mm(self) -> float:
        return float(self._position)

    def boot_check(self) -> None:
        log.info("  [MOCK] Gantry: boot check passed")


class MockGripper:
    """Mock gripper driver for testing without hardware."""

    def home(self) -> None:
        log.info("  [MOCK] Gripper: homing")
        time.sleep(0.5)
        log.info("  [MOCK] Gripper: homed")

    def close(self) -> None:
        log.info("  [MOCK] Gripper: closing (GRAB)")
        time.sleep(1.0)
        log.info("  [MOCK] Gripper: closed")

    def open(self) -> None:
        log.info("  [MOCK] Gripper: opening (RELEASE)")
        time.sleep(1.0)
        log.info("  [MOCK] Gripper: open")


class MockExtruder:
    """Mock extruder driver for testing without hardware."""

    def __init__(self):
        self._plunger_met: bool = False

    @property
    def is_plunger_met(self) -> bool:
        return self._plunger_met

    def home(self) -> None:
        log.info("  [MOCK] Extruder: homing")
        time.sleep(0.5)
        self._plunger_met = False
        log.info("  [MOCK] Extruder: homed")

    def meet_plunger(self) -> None:
        log.info("  [MOCK] Extruder: meeting plunger...")
        time.sleep(1.0)
        self._plunger_met = True
        log.info("  [MOCK] Extruder: plunger contact confirmed")

    def dispense(self, speed: str = "medium") -> None:
        log.info(f"  [MOCK] Extruder: dispensing at speed '{speed}'")

    def stop_dispense(self) -> None:
        log.info("  [MOCK] Extruder: dispense stopped")

    def retract(self) -> None:
        log.info("  [MOCK] Extruder: retracting")
        time.sleep(1.0)
        self._plunger_met = False
        log.info("  [MOCK] Extruder: retracted")


class MockConveyor:
    """Mock conveyor driver for testing without hardware."""

    def __init__(self):
        self._position = "HOME"

    def home(self) -> None:
        log.info("  [MOCK] Conveyor: homing")
        time.sleep(0.3)
        self._position = "HOME"
        log.info("  [MOCK] Conveyor: homed")

    def move_to_station(self, station: str) -> None:
        log.info(f"  [MOCK] Conveyor: moving to {station} station")
        time.sleep(0.5)
        self._position = station.upper()
        log.info(f"  [MOCK] Conveyor: arrived at {station}")

    def move_forward(self, distance_mm: int) -> None:
        log.info(f"  [MOCK] Conveyor: moving forward {distance_mm}mm")
        time.sleep(0.3)
        log.info("  [MOCK] Conveyor: forward move complete")

    def move_reverse(self, distance_mm: int) -> None:
        log.info(f"  [MOCK] Conveyor: moving reverse {distance_mm}mm")
        time.sleep(0.3)
        log.info("  [MOCK] Conveyor: reverse move complete")

    def start_zigzag(self) -> None:
        log.info("  [MOCK] Conveyor: starting zigzag")

    def stop_zigzag(self) -> None:
        log.info("  [MOCK] Conveyor: stopping zigzag")
        time.sleep(0.2)
        log.info("  [MOCK] Conveyor: zigzag stopped")

    def start(self, speed: int) -> None:
        log.info(f"  [MOCK] Conveyor: starting forward at speed {speed}")

    def reverse(self, speed: int) -> None:
        log.info(f"  [MOCK] Conveyor: starting reverse at speed {speed}")

    def stop(self) -> None:
        log.info("  [MOCK] Conveyor: stopped")

    def cylinder_grab(self) -> None:
        log.info("  [MOCK] Conveyor: cylinder GRAB")
        time.sleep(0.5)
        log.info("  [MOCK] Conveyor: cylinder at grab position")

    def cylinder_drop(self) -> None:
        log.info("  [MOCK] Conveyor: cylinder DROP")
        time.sleep(0.5)
        log.info("  [MOCK] Conveyor: cylinder at drop position")

    def lamp_on(self) -> None:
        log.info("  [MOCK] Conveyor: lamp ON")

    def lamp_off(self) -> None:
        log.info("  [MOCK] Conveyor: lamp OFF")
