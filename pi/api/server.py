"""
api/server.py

FastAPI server — sits between the Chromium UI and the order_manager.

Endpoints:
    POST /api/dispense          — submit a new order from the UI
    GET  /api/status/{order_id} — poll order progress
    GET  /api/levels            — list valid coverage levels (for the UI)

Run with:
    uvicorn pi.api.server:app --host 0.0.0.0 --port 8080

The UI (Chromium) talks to this on localhost:8080.
This server talks to order_manager directly as a Python function call.
"""

import os
import subprocess
import threading
import time

import serial
import serial.serialutil
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pi.ordering.order_manager import OrderManager, OrderStatus
from pi.ordering.sauce_config import get_coverage_levels
from pi.utils.logger import log, get_recent_logs
from pi.motion.arduino_controller import ArduinoController

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="SauceBot API")

# Allow the browser to make requests to localhost
# (needed because Chromium enforces CORS even on localhost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the UI static files at the root
# So http://localhost:8080/ loads index.html automatically
ui_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../ui'))
app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

# ─── Serial (Arduino) ───────────────────────────────────────────────────────

import sys
_SERIAL_PORT    = "COM4" if sys.platform == "win32" else "/dev/ttyACM0"
_SERIAL_BAUD    = 9600
_SERIAL_TIMEOUT = 2.0   # seconds to wait for Arduino response line
_ARDUINO_RESET_DELAY = 2.0  # Arduino resets when serial opens; let it boot

_serial_lock: threading.Lock = threading.Lock()
_ser: serial.Serial | None = None


@app.on_event("startup")
def _open_serial() -> None:
    # Gantry controlled by VESCGantry via USB serial — serial port owned by that driver. Skip.
    pass


def _send_serial_command(command: str) -> str:
    """Send a newline-terminated command and return the Arduino's response line."""
    if _ser is None or not _ser.is_open:
        raise HTTPException(
            status_code=503,
            detail=f"Serial device {_SERIAL_PORT} not available",
        )
    with _serial_lock:
        _ser.reset_input_buffer()
        _ser.write(f"{command}\n".encode())
        raw = _ser.readline()
    response = raw.decode(errors="replace").strip()
    if not response:
        raise HTTPException(status_code=502, detail="No response from Arduino (timeout)")
    return response


@app.post("/sauce/start")
def sauce_start():
    """Send ON over serial → Arduino turns on LED_BUILTIN."""
    response = _send_serial_command("ON")
    return {"success": True, "command": "ON", "arduino_response": response}


@app.post("/sauce/stop")
def sauce_stop():
    """Send OFF over serial → Arduino turns off LED_BUILTIN."""
    response = _send_serial_command("OFF")
    return {"success": True, "command": "OFF", "arduino_response": response}


# ─── Dependency injection ─────────────────────────────────────────────────────
# The order_manager is set once at startup by main.py
# All endpoints read from this module-level variable
_order_manager: OrderManager | None = None

def set_order_manager(om: OrderManager) -> None:
    global _order_manager
    _order_manager = om

def get_order_manager() -> OrderManager:
    if _order_manager is None:
        raise RuntimeError("OrderManager not initialised — call set_order_manager() at startup")
    return _order_manager


# ─── Request / response models ────────────────────────────────────────────────

class DispenseRequest(BaseModel):
    level: str      # "light" | "medium" | "heavy"

class DispenseResponse(BaseModel):
    order_id: str
    status: str     # "QUEUED"

class StatusResponse(BaseModel):
    order_id: str
    status: str     # "QUEUED" | "PROCESSING" | "DONE" | "FAILED"
    error: str | None = None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/api/dispense", response_model=DispenseResponse)
def dispense(req: DispenseRequest):
    """
    Called by the UI when the user presses START.
    Queues a new order and returns an order_id immediately.
    The UI then polls /api/status/{order_id} until status is DONE or FAILED.
    """
    log.info(f"API: received '{req.level}' — sending to order manager")

    try:
        order_id = get_order_manager().submit_order(req.level)
    except ValueError as e:
        # Unknown level — tell the UI clearly
        raise HTTPException(status_code=400, detail=str(e))

    return DispenseResponse(order_id=order_id, status="QUEUED")


@app.get("/api/status/{order_id}", response_model=StatusResponse)
def get_status(order_id: str):
    """
    Called by the UI every second to check order progress.
    Returns current status and any error message.
    """
    order = get_order_manager().get_status(order_id)

    if order is None:
        raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")

    return StatusResponse(
        order_id=order.order_id,
        status=order.status.name,
        error=order.error,
    )


@app.get("/api/levels")
def get_levels():
    """
    Returns the valid coverage levels.
    The UI can call this on load to build its buttons dynamically in future.
    """
    return {"levels": get_coverage_levels()}


@app.get("/api/logs")
def get_logs():
    """Returns recent log entries for the UI log panel."""
    return {"logs": get_recent_logs()}


@app.get("/api/health")
def health():
    """Simple health check — useful during development."""
    return {"status": "ok"}


# ─── Debug endpoints ──────────────────────────────────────────────────────────

# Match the full-travel targets from gripper.py and extruder.py
_GRIPPER_CLOSE_TICKS  = -int(2.75 * 753)   # -2070
_EXTRUDER_DISPENSE_TICKS = -int(2.0 * 753)  # -1506


@app.post("/api/debug/test-gripper")
def debug_test_gripper():
    """Full gripper cycle — close fully then open fully."""
    try:
        arduino = ArduinoController()
        if not arduino.send_command(f"MOVE_GRIPPER:{_GRIPPER_CLOSE_TICKS}", timeout=15.0):
            raise RuntimeError("MOVE_GRIPPER (close) timed out")
        if not arduino.send_command("MOVE_GRIPPER:0", timeout=15.0):
            raise RuntimeError("MOVE_GRIPPER:0 (open) timed out")
        log.info("Debug: gripper full cycle complete")
        return {"success": True}
    except Exception as e:
        log.error(f"Debug gripper test failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/debug/test-extruder")
def debug_test_extruder():
    """Full extruder cycle — dispense fully then retract fully."""
    try:
        arduino = ArduinoController()
        if not arduino.send_command(f"MOVE_EXTRUDER:{_EXTRUDER_DISPENSE_TICKS}", timeout=45.0):
            raise RuntimeError("MOVE_EXTRUDER (dispense) timed out")
        if not arduino.send_command("MOVE_EXTRUDER:0", timeout=45.0):
            raise RuntimeError("MOVE_EXTRUDER:0 (retract) timed out")
        log.info("Debug: extruder full cycle complete")
        return {"success": True}
    except Exception as e:
        log.error(f"Debug extruder test failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/debug/restart")
def debug_restart():
    """Restarts the sauce-backend systemd service."""
    def _do_restart():
        subprocess.run(["sudo", "systemctl", "restart", "sauce-backend"], check=False)
    threading.Thread(target=_do_restart, daemon=True).start()
    return {"success": True, "message": "Restarting..."}


# ─── Manual control endpoints ─────────────────────────────────────────────────

def _manual(command: str, timeout: float = 20.0):
    """Send a single Arduino command and return success/failure."""
    try:
        arduino = ArduinoController()
        if not arduino.send_command(command, timeout=timeout):
            raise RuntimeError(f"{command} timed out")
        log.info(f"Manual: {command} complete")
        return {"success": True}
    except Exception as e:
        log.error(f"Manual {command} failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/manual/home-grabber")
def manual_home_grabber():
    return _manual("HOME_GRIPPER")

@app.post("/api/manual/home-extruder")
def manual_home_extruder():
    return _manual("HOME_EXTRUDER")

@app.post("/api/manual/close-grabber")
def manual_close_grabber():
    return _manual("CLOSE_GRABBER")

@app.post("/api/manual/open-grabber")
def manual_open_grabber():
    return _manual("OPEN_GRABBER")

@app.post("/api/manual/open-extruder")
def manual_open_extruder():
    return _manual("OPEN_EXTRUDER")

@app.post("/api/manual/meet-plunger")
def manual_meet_plunger():
    return _manual("MEET_PLUNGER", timeout=45.0)
