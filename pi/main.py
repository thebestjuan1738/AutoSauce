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


def build_order_manager() -> OrderManager:
    """
    Swap Mock* for real GPIO drivers once hardware is wired.
    Nothing else in the codebase needs to change.
    """
    return OrderManager(
        gantry=MockGantry(),
        gripper=MockGripper(),
        extruder=MockExtruder(),
        conveyor=MockConveyor(),
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
