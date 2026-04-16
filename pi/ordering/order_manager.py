"""
order_manager.py

Manages the order queue and executes the full motion sequence for each order.
Runs the sequence in a background thread so the UI stays responsive.

Order lifecycle:
    QUEUED → PROCESSING → DONE
                       → FAILED

Motion sequence per order:
    1.  Gantry → dock
    2.  Gripper close
    3.  Gantry → dispense
    4.  Conveyor + extruder + gantry dispense sweep start simultaneously
    5.  Extruder finishes; sweep stops at current position
    6.  Conveyor finishes (longer duration)
    7.  Gantry → dock
    8.  Gripper open
    9.  Gantry → home

The UI calls submit_order() and polls get_status() — that's the entire
public interface. Nothing else in this file is meant to be called directly.
"""

import queue
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from pi.ordering.sauce_config import get_profile, POSITIONS, DISPENSE_SWEEP_START_MM, DISPENSE_SWEEP_END_MM
from pi.utils.logger import log


class OrderStatus(Enum):
    QUEUED     = auto()
    PROCESSING = auto()
    DONE       = auto()
    FAILED     = auto()


@dataclass
class Order:
    order_id: str
    level: str                              # "light" | "medium" | "heavy"
    status: OrderStatus = OrderStatus.QUEUED
    error: Optional[str] = None


class OrderManager:
    """
    Owns the order queue and the background worker thread.
    Depends on injected driver objects so it can be tested without hardware.
    """

    def __init__(self, gantry, gripper, extruder, conveyor):
        """
        Args:
            gantry   — has move_to(position_mm: int) -> None
            gripper  — has home(), close(), and open()
            extruder — has dispense(duration_ms: int) -> None
            conveyor — has start(speed: int), stop() -> None
        """
        self._gantry  = gantry
        self._gripper = gripper
        self._extruder = extruder
        self._conveyor = conveyor

    @property
    def gantry(self):
        return self._gantry

        self._queue: queue.Queue[Order] = queue.Queue()
        self._orders: dict[str, Order] = {}
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, daemon=True)

    # ─── Public interface (called by UI) ──────────────────────────────────────

    def start(self) -> None:
        """Start the background worker. Call once at application startup."""
        log.info("OrderManager: starting worker thread")
        self._worker.start()

    def submit_order(self, level: str) -> str:
        """
        Queue a new order. Returns the order_id immediately.
        The UI should poll get_status(order_id) to track progress.

        Args:
            level: "light" | "medium" | "heavy"

        Returns:
            order_id string
        """
        get_profile(level)  # raises ValueError early if level is invalid

        order_id = str(uuid.uuid4())
        order = Order(order_id=order_id, level=level)

        with self._lock:
            self._orders[order_id] = order

        self._queue.put(order)
        log.info(f"Order queued: {order_id} ({level})")
        return order_id

    def get_status(self, order_id: str) -> Optional[Order]:
        """Return the current Order object, or None if not found."""
        return self._orders.get(order_id)

    # ─── Worker thread ────────────────────────────────────────────────────────

    def _run(self) -> None:
        """Continuously pull orders from the queue and process them one at a time."""
        while True:
            order = self._queue.get()
            self._process(order)
            self._queue.task_done()

    def _process(self, order: Order) -> None:
        """Execute the full motion sequence for one order."""
        log.info(f"Processing order {order.order_id} — level: {order.level}")
        order.status = OrderStatus.PROCESSING

        try:
            profile = get_profile(order.level)
            self._run_sequence(profile)
            order.status = OrderStatus.DONE
            log.info(f"Order {order.order_id} DONE")

        except Exception as e:
            order.status = OrderStatus.FAILED
            order.error = str(e)
            log.error(f"Order {order.order_id} FAILED: {e}")
            self._safe_abort()

    def _run_sequence(self, profile: dict) -> None:
        """
        The motion sequence. Steps are numbered to match the docstring above.
        Each step blocks until complete before moving to the next,
        except steps 4/5 where conveyor and extruder run concurrently.
        """
        # 1. Travel to dock
        log.info("Step 1: gantry → dock")
        self._gantry.move_to(POSITIONS["dock"])

        # 2. Close gripper — pick up sauce dispenser
        log.info("Step 2: gripper close")
        self._gripper.close()

        # 3. Travel to dispense position
        log.info("Step 3: gantry → dispense")
        self._gantry.move_to(POSITIONS["dispense"])

        # 4+5. Conveyor, extruder, and gantry sweep run simultaneously.
        # Gantry travels from DISPENSE_SWEEP_START_MM to DISPENSE_SWEEP_END_MM
        # while the extruder is running for even sauce coverage across the sandwich.
        # Extruder blocks this thread; when it finishes the stop event is set
        # and the sweep thread completes its current move then stops.
        # Conveyor is joined before retract.
        log.info("Step 4+5: conveyor + extruder + dispense sweep start")
        stop_sweep = threading.Event()
        conveyor_thread = threading.Thread(
            target=self._run_conveyor,
            args=(profile["conveyor_speed"], profile["conveyor_ms"]),
            daemon=True,
        )
        sweep_thread = threading.Thread(
            target=self._run_dispense_sweep,
            args=(stop_sweep,),
            daemon=True,
        )
        conveyor_thread.start()
        sweep_thread.start()
        self._extruder.dispense()                        # blocks until done
        stop_sweep.set()
        sweep_thread.join()                              # finishes current move
        conveyor_thread.join()                           # wait for belt to finish
        log.info("Step 6: conveyor + sweep done")
        self._extruder.retract()                         # retract plunger after belt clears

        # 7. Return to dock
        log.info("Step 7: gantry → dock")
        self._gantry.move_to(POSITIONS["dock"])

        # 8. Open gripper — return sauce dispenser to dock
        log.info("Step 8: gripper open")
        self._gripper.open()

        # 9. Return to home
        log.info("Step 9: gantry → home")
        self._gantry.move_to(POSITIONS["home"])

    def _run_conveyor(self, speed: int, duration_ms: int) -> None:
        """Runs on its own thread — forward for first half, reverse for second half."""
        import time
        half = duration_ms / 2 / 1000
        self._conveyor.start(speed)
        time.sleep(half)
        if hasattr(self._conveyor, 'reverse'):
            self._conveyor.reverse(speed)
            time.sleep(half)
        self._conveyor.stop()

    def _run_dispense_sweep(self, stop: threading.Event) -> None:
        """
        Sweep the gantry from DISPENSE_SWEEP_START_MM to DISPENSE_SWEEP_END_MM
        (and back, repeating) until stop is set. Stops after completing whichever
        move_to() is in progress when the event fires.
        """
        positions = [DISPENSE_SWEEP_START_MM, DISPENSE_SWEEP_END_MM]
        idx = 0
        while not stop.is_set():
            self._gantry.move_to(positions[idx])
            idx = 1 - idx   # alternate between start and end
        log.info("Dispense sweep: stopped at %dmm", positions[idx])

    def _safe_abort(self) -> None:
        """Best-effort cleanup after a failure. Tries to stop all actuators."""
        log.warning("Aborting — stopping all actuators")
        try:
            self._conveyor.stop()
        except Exception:
            pass
        try:
            self._gantry.move_to(POSITIONS["home"])
        except Exception:
            pass
