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

# True → use real VESCGantry (NEO rev via USB VESC) even when USE_MOCK is True.
# Everything else stays mocked — lets you test the gantry motor in isolation.
# Can also be overridden via the USE_VESC_GANTRY environment variable:
#   USE_VESC_GANTRY=0  → fall back to MockGantry (no serial port needed)
#   USE_VESC_GANTRY=1  → open /dev/ttyACM0 (requires --device flag in Docker)
USE_VESC_GANTRY = os.environ.get("USE_VESC_GANTRY", "1") != "0"

# True → use real GPIOGripper + GPIOExtruder (Arduino over USB) even when USE_MOCK is True.
# Can be overridden via the USE_ARDUINO environment variable:
#   USE_ARDUINO=0  → fall back to MockGripper/MockExtruder (no Arduino needed)
#   USE_ARDUINO=1  → open /dev/ttyACM0 or /dev/ttyUSB0 (requires --device flag in Docker)
USE_ARDUINO = os.environ.get("USE_ARDUINO", "1") != "0"


def build_order_manager() -> OrderManager:
    if USE_MOCK and not USE_VESC_GANTRY:
        return OrderManager(
            gantry=MockGantry(),
            gripper=MockGripper(),
            extruder=MockExtruder(),
            conveyor=MockConveyor(),
        )

    # Lazy import so the serial port is only opened when actually needed.
    import serial.serialutil
    from pi.motion.vesc_gantry import VESCGantry
    try:
        vesc_gantry = VESCGantry()
        vesc_gantry.boot_check()
    except serial.serialutil.SerialException as exc:
        if not USE_MOCK:
            # Real-driver mode: a missing VESC is fatal.
            raise
        log.warning(
            f"VESCGantry unavailable ({exc}). "
            "Falling back to MockGantry. "
            "Pass --device /dev/ttyACM0:/dev/ttyACM0 to docker run "
            "or set USE_VESC_GANTRY=0 to silence this warning."
        )
        vesc_gantry = MockGantry()

    # Build gripper + extruder — real Arduino or mock.
    if USE_ARDUINO or not USE_MOCK:
        from pi.motion.gripper  import GPIOGripper
        from pi.motion.extruder import GPIOExtruder
        try:
            gripper  = GPIOGripper()
            extruder = GPIOExtruder()
        except Exception as exc:
            if not USE_MOCK:
                raise
            log.warning(
                f"Arduino unavailable ({exc}). "
                "Falling back to MockGripper/MockExtruder. "
                "Set USE_ARDUINO=0 to silence this warning."
            )
            gripper  = MockGripper()
            extruder = MockExtruder()
    else:
        gripper  = MockGripper()
        extruder = MockExtruder()

    if USE_MOCK:
        # Hardware-in-the-loop: real VESC gantry + optional real Arduino, conveyor still mocked.
        return OrderManager(
            gantry=vesc_gantry,
            gripper=gripper,
            extruder=extruder,
            conveyor=MockConveyor(),
        )

    # Full real drivers (Pi only).
    from pi.motion.extruder import GPIOExtruder
    from pi.motion.gripper  import GPIOGripper
    from pi.motion.conveyor import GPIOConveyor
    return OrderManager(
        gantry=vesc_gantry,
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
