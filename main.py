"""
main.py — SauceBot entry point.

Starts up in this order:
  1. Build OrderManager with mock (or real) drivers
  2. Inject it into the FastAPI server
  3. Start the OrderManager background worker thread
  4. Start uvicorn (serves both the UI and the API on port 8080)

Chromium kiosk (launch.sh) opens http://localhost:8080/ui
which loads index.html from the ui/ folder.
"""

import os

import uvicorn
from pi.ordering.order_manager import OrderManager
from pi.motion.mock_drivers import MockGantry, MockGripper, MockExtruder, MockConveyor
from pi.api.server import app, set_order_manager
from pi.utils.logger import log

# ── Driver toggle ──────────────────────────────────────────────────────────────
# True  → mock drivers (no hardware needed, runs on any machine)
# False → real GPIO drivers (Pi only, hardware must be wired and VESC configured)
USE_MOCK = True

# True → use real VESCConveyor (NEO rev via USB VESC) even when USE_MOCK is True.
# Everything else stays mocked — lets you test the sandwich motor in isolation.
# Can also be overridden via the USE_VESC_CONVEYOR environment variable:
#   USE_VESC_CONVEYOR=0  → fall back to MockConveyor (no serial port needed)
#   USE_VESC_CONVEYOR=1  → open /dev/ttyACM0 (requires --device flag in Docker)
USE_VESC_CONVEYOR = os.environ.get("USE_VESC_CONVEYOR", "1") != "0"


def build_order_manager() -> OrderManager:
    from pi.motion.mock_drivers import MockGantry, MockExtruder, MockConveyor
    from pi.motion.gripper import GPIOGripper
    return OrderManager(
        gantry=MockGantry(),
        gripper=GPIOGripper(),
        extruder=MockExtruder(),
        conveyor=MockConveyor(),
    )

    # Lazy import so pyvesc is only loaded when actually needed.
    import serial.serialutil
    from pi.motion.vesc_conveyor import VESCConveyor
    try:
        conveyor = VESCConveyor()
        conveyor.boot_check()
    except serial.serialutil.SerialException as exc:
        if not USE_MOCK:
            # Real-driver mode: a missing VESC is fatal.
            raise
        log.warning(
            f"VESCConveyor unavailable ({exc}). "
            "Falling back to MockConveyor. "
            "Pass --device /dev/ttyACM0:/dev/ttyACM0 to docker run "
            "or set USE_VESC_CONVEYOR=0 to silence this warning."
        )
        conveyor = MockConveyor()

    if USE_MOCK:
        # Hardware-in-the-loop: real VESC conveyor, everything else mocked.
        return OrderManager(
            gantry=MockGantry(),
            gripper=MockGripper(),
            extruder=MockExtruder(),
            conveyor=conveyor,
        )

    # Full real drivers (Pi only).
    from pi.motion.gantry   import GPIOGantry
    from pi.motion.extruder import GPIOExtruder
    from pi.motion.gripper  import GPIOGripper
    from pi.motion.conveyor import GPIOConveyor
    return OrderManager(
        gantry=GPIOGantry(),
        gripper=GPIOGripper(),
        extruder=GPIOExtruder(),
        conveyor=GPIOConveyor(),
    )


def main():
    log.info("=== SauceBot starting ===")

    om = build_order_manager()
    set_order_manager(om)   # give the API access to the order manager
    om.start()              # start the background worker thread

    log.info("Starting API server on http://localhost:8080")
    log.info("UI available at http://localhost:8080/ui")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
