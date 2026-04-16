"""
order_manager.py

Manages the order queue and executes the full motion sequence for each order.
Runs the sequence in a background thread so the UI stays responsive.

Order lifecycle:
    QUEUED → PROCESSING → DONE
                       → FAILED

Motion sequence per order:
    1.  Gantry → dock
    2.  Gripper close (grab bottle)
    3.  Gantry → dispense
    4.  Extruder: MEET_PLUNGER — drive until pad contact confirmed (blocking)
    5.  Conveyor + extruder DISPENSE_SAUCE + slow gantry sweep simultaneously
    6.  Extruder finishes; conveyor finishes
    7.  Extruder retract
    8.  Gantry → home
    9.  Gantry → dock
    10. Gripper open (return bottle)

The UI calls submit_order() and polls get_status() — that's the entire
public interface. Nothing else in this file is meant to be called directly.
"""

import queue
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from pi.ordering.sauce_config import get_profile, POSITIONS, DISPENSE_SWEEP_END_MM
from pi.motion.vesc_gantry import SWEEP_MAX_DUTY
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

        self._queue: queue.Queue[Order] = queue.Queue()
        self._orders: dict[str, Order] = {}
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, daemon=True)

    @property
    def gantry(self):
        return self._gantry

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
        The motion sequence. Steps are numbered to match the class docstring.
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

        # 4. Wait for extruder to make contact with the pad before moving.
        log.info("Step 4: extruder → meet plunger")
        self._extruder.meet_plunger()

        # 5+6. Conveyor, extruder dispense, and slow gantry sweep simultaneously.
        # Contact is already confirmed — extruder now pushes the fixed dispense amount
        # while the gantry sweeps slowly for even coverage.
        # Extruder blocks this thread; conveyor and sweep threads are joined after.
        log.info("Step 5+6: conveyor + extruder dispense + slow gantry sweep")
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
        log.info("Step 6+7: conveyor + sweep done")
        self._extruder.retract()                         # retract plunger after belt clears

        # 8. Return to home first (safe resting position)
        log.info("Step 8: gantry → home")
        self._gantry.move_to(POSITIONS["home"])

        # 9. Travel to dock to return the bottle
        log.info("Step 9: gantry → dock")
        self._gantry.move_to(POSITIONS["dock"])

        # 10. Open gripper — release sauce dispenser at dock
        log.info("Step 10: gripper open")
        self._gripper.open()

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
        Single slow sweep from the current dispense position to
        DISPENSE_SWEEP_END_MM while the extruder dispenses.
        Uses SWEEP_MAX_DUTY so the gantry moves slowly for even sauce coverage.
        """
        self._gantry.move_to(DISPENSE_SWEEP_END_MM, max_duty=SWEEP_MAX_DUTY)
        log.info("Dispense sweep: complete at %dmm", DISPENSE_SWEEP_END_MM)

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
