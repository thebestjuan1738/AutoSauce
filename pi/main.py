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

import uvicorn
from pi.ordering.order_manager import OrderManager
from pi.motion.mock_drivers import MockGantry, MockGripper, MockExtruder, MockConveyor
from pi.api.server import app, set_order_manager
from pi.utils.logger import log

# ── Driver toggle ──────────────────────────────────────────────────────────────
# True  → mock drivers (no hardware needed, runs on any machine)
# False → real GPIO drivers (Pi only, hardware must be wired and VESC configured)
USE_MOCK = True


def build_order_manager() -> OrderManager:
    if USE_MOCK:
        return OrderManager(
            gantry=MockGantry(),
            gripper=MockGripper(),
            extruder=MockExtruder(),
            conveyor=MockConveyor(),
        )

    # Lazy import so RPi.GPIO / pyvesc are never loaded when USE_MOCK is True.
    # These packages only exist on the Pi — importing them on a dev machine
    # would crash immediately.
    from pi.motion.gpio_drivers import (
        GPIOGantry,
        GPIOExtruder,
        GPIOGripper,
        GPIOConveyor,
    )
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
