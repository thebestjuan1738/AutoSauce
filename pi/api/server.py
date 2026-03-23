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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pi.ordering.order_manager import OrderManager, OrderStatus
from pi.ordering.sauce_config import get_coverage_levels
from pi.utils.logger import log

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
app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")

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
    log.info(f"API: dispense request — level='{req.level}'")

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


@app.get("/api/health")
def health():
    """Simple health check — useful during development."""
    return {"status": "ok"}
