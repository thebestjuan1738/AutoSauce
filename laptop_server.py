"""
laptop_server.py — Entry point for running AutoSauce on a laptop.

All 3 microcontrollers connect to the laptop via USB.  The Pi touchscreen
browser points at http://THIS_LAPTOP_IP:8080/ui instead of localhost.

Run from the repo root:
    python laptop_server.py

Dependencies:
    pip install fastapi uvicorn pyserial
"""

import uvicorn

from pi.ordering.order_manager import OrderManager
from pi.motion.vesc_gantry    import VESCGantry
from pi.motion.gripper        import GPIOGripper
from pi.motion.extruder       import GPIOExtruder
from pi.motion.conveyor       import GPIOConveyor
from pi.api.server            import app, set_order_manager, set_gantry
from pi.utils.logger          import log


def build_order_manager() -> OrderManager:
    log.info("Initializing gantry (VESCGantry)...")
    gantry = VESCGantry()
    gantry.boot_check()

    log.info("Initializing printhead (gripper + extruder)...")
    gripper  = GPIOGripper()
    extruder = GPIOExtruder()

    log.info("Initializing conveyor...")
    conveyor = GPIOConveyor()

    return OrderManager(
        gantry=gantry,
        gripper=gripper,
        extruder=extruder,
        conveyor=conveyor,
    )


def main():
    log.info("=== SauceBot starting (laptop mode) ===")

    om = build_order_manager()

    set_order_manager(om)
    set_gantry(om._gantry)

    om.start()

    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    log.info("Starting API server — point Pi browser at http://%s:8080/ui", local_ip)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
