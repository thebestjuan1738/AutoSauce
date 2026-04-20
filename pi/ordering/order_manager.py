"""
order_manager.py

Manages the order queue and executes the full motion sequence for each order.
Runs the sequence in a background thread so the UI stays responsive.

Orchestrates all 3 microcontrollers:
    - GantryCode (ESP8266)      : Linear gantry positioning
    - PrintheadCode (Mega)      : Gripper + Extruder control
    - ConveyorHotdogCode (Uno)  : Conveyor belt + Cylinder + Heat lamp

Order lifecycle:
    QUEUED → PROCESSING → DONE
                       → FAILED

Full motion sequence per order:
    1.  User selects sauce level (light/medium/heavy) on touchscreen
    2.  User clicks start on touchscreen
    3.  Conveyor: Home (zero position)
    4.  Conveyor: Move to HOTDOG station
    5.  Cylinder: GRAB, wait 1 sec, DROP
    6.  Conveyor: Move to HEAT station
    7.  Lamp: ON for 10 seconds, then OFF
    8.  Conveyor: Move to SAUCE station
    9.  If plunger NOT already met: Gantry → dock, Gripper close, Extruder MEETPLUNGER
    10. Gantry: Move to sauce start position
    11. Concurrent: Zigzag + Gantry sweep to sauce end + Extrude at user speed
    12. When gantry reaches end: stop zigzag and extruding
    13. Wait 5 seconds
    14. Conveyor: Move to PICKUP station
    15. Gripper: Release (drop bottle)
    16. Gantry: Move to 2 inches (51mm)

The UI calls submit_order() and polls get_status() — that's the entire
public interface. Nothing else in this file is meant to be called directly.
"""

import queue
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from pi.ordering.sauce_config import get_profile, POSITIONS, DISPENSE_SWEEP_END_MM

# How many seconds before the gantry reaches end to stop extrusion.
# Tune this to compensate for extruder coast/lag.
EXTRUDER_EARLY_STOP_S = 0.4
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
            gantry   — VESCGantry: move_to(position_mm), home(), get_position_mm()
            gripper  — GPIOGripper: home(), close(), open()
            extruder — GPIOExtruder: home(), meet_plunger(), dispense(), stop_dispense(), retract()
            conveyor — GPIOConveyor: home(), move_to_station(), start_zigzag(), stop_zigzag(),
                                     cylinder_grab(), cylinder_drop(), lamp_on(), lamp_off()
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
        log.info("hi this is adam's test")
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
            self._run_sequence(order.level)
            order.status = OrderStatus.DONE
            log.info(f"Order {order.order_id} DONE")

        except Exception as e:
            order.status = OrderStatus.FAILED
            order.error = str(e)
            log.error(f"Order {order.order_id} FAILED: {e}")
            self._safe_abort()

    def _run_sequence(self, level: str) -> None:
        """
        The full motion sequence orchestrating all 3 microcontrollers.
        Steps are numbered to match the user's workflow specification.

        Args:
            level: "light" | "medium" | "heavy" - determines extrude speed
        """
        # Extruder always runs at medium speed; gantry sweep speed varies by level
        extruder_speed = "medium"
        level_to_sweep_ips = {
            "light":  5.0,
            "medium": 3.0,
            "heavy":  1.0,
        }
        sweep_speed_ips = level_to_sweep_ips.get(level, 2.5)

        # ═══════════════════════════════════════════════════════════════════════
        # STEPS 3-5: HOTDOG LOADING (Conveyor Controller)
        # ═══════════════════════════════════════════════════════════════════════

        # Step 3: Conveyor homes
        log.info("Step 3: Conveyor → home")
        self._conveyor.home()

        # Step 4: Conveyor moves to hotdog station
        log.info("Step 4: Conveyor → HOTDOG station")
        self._conveyor.move_to_station("HOTDOG")

        # Step 5: Cylinder grab, wait 1 sec, drop
        log.info("Step 5: Cylinder GRAB")
        self._conveyor.cylinder_grab()
        log.info("Step 5: Waiting 1 second...")
        time.sleep(1.0)
        log.info("Step 5: Cylinder DROP")
        self._conveyor.cylinder_drop()

        # ═══════════════════════════════════════════════════════════════════════
        # STEPS 6-8: HEATING & MOVE TO SAUCE (Conveyor Controller)
        # ═══════════════════════════════════════════════════════════════════════

        # Step 6: Conveyor moves to heat station
        log.info("Step 6: Conveyor → HEAT station")
        self._conveyor.move_to_station("HEAT")

        # Step 7: Heat lamp on for 10 seconds
        log.info("Step 7: Lamp ON (10 seconds)")
        self._conveyor.lamp_on()
        time.sleep(10.0)
        self._conveyor.lamp_off()
        log.info("Step 7: Lamp OFF")

        # Step 8: Conveyor moves to sauce station
        log.info("Step 8: Conveyor → SAUCE station")
        self._conveyor.move_to_station("SAUCE")

        # ═══════════════════════════════════════════════════════════════════════
        # STEPS 9-12: PREPARE FOR DISPENSING (Gantry + Printhead)
        # ═══════════════════════════════════════════════════════════════════════

        # Steps 9-11 are skipped if the extruder is already at plunger contact
        # (i.e. the previous order left it extended — no retract between orders).
        if not self._extruder.is_plunger_met:
            # Step 9: Gantry moves to dock
            log.info("Step 9: Gantry → dock")
            self._gantry.move_to(POSITIONS["dock"])

        # Step 10: Gripper always closes (may have been released at end of previous order)
        log.info("Step 10: Gripper → CLOSE (grab bottle)")
        self._gripper.close()

        if not self._extruder.is_plunger_met:
            # Step 11: Extruder meets plunger (before gantry moves to dispense position)
            log.info("Step 11: Extruder → MEETPLUNGER")
            self._extruder.meet_plunger()
        else:
            log.info("Steps 9+11: Skipping dock and meet plunger — plunger already at contact")

        # Step 12: Gantry moves to sauce start (dispense position)
        log.info("Step 12: Gantry → sauce start (dispense position)")
        self._gantry.move_to(POSITIONS["dispense"])

        # ═══════════════════════════════════════════════════════════════════════
        # STEPS 13-14: CONCURRENT DISPENSING
        # ═══════════════════════════════════════════════════════════════════════

        # Step 13: Start concurrent operations
        log.info(f"Step 13: Starting concurrent dispense (speed={extruder_speed})")

        # Start conveyor zigzag
        self._conveyor.start_zigzag()

        # Start extruder dispensing (always slow)
        self._extruder.dispense(speed=extruder_speed)
        time.sleep(0.75)

        # Schedule extruder stop slightly before the gantry reaches its end position
        # so sauce doesn't over-run past the hotdog.
        sweep_distance_in = (DISPENSE_SWEEP_END_MM - POSITIONS["dispense"]) / 25.4
        sweep_duration_s = sweep_distance_in / sweep_speed_ips
        early_stop_delay_s = max(0.0, sweep_duration_s - EXTRUDER_EARLY_STOP_S)
        log.info(
            f"Step 13: Extruder will stop in {early_stop_delay_s:.2f}s "
            f"({EXTRUDER_EARLY_STOP_S}s before estimated gantry end)"
        )
        stop_timer = threading.Timer(early_stop_delay_s, self._extruder.stop_dispense)
        stop_timer.start()

        # Gantry sweeps to sauce end at level-dependent speed (this blocks until complete)
        log.info(f"Step 13: Gantry sweeping to sauce end at {sweep_speed_ips} in/s...")
        try:
            self._gantry.move_to(DISPENSE_SWEEP_END_MM, speed_ips=sweep_speed_ips)
        finally:
            stop_timer.cancel()          # no-op if it already fired
            self._extruder.stop_dispense()  # ensure stopped regardless

        # Step 14: When gantry reaches end, stop zigzag
        log.info("Step 14: Gantry reached end — stopping zigzag")
        self._conveyor.stop_zigzag()

        # ═══════════════════════════════════════════════════════════════════════
        # STEPS 15-16: FINISH UP
        # ═══════════════════════════════════════════════════════════════════════

        # Step 15: Wait 5 seconds
        log.info("Step 15: Waiting 5 seconds...")
        time.sleep(5.0)

        # Step 16: Conveyor moves to pickup station
        log.info("Step 16: Conveyor → PICKUP station")
        self._conveyor.move_to_station("PICKUP")

        # Step 17: Return gantry to dispense start position
        log.info("Step 17: Gantry → dispense start position")
        self._gantry.move_to(POSITIONS["dispense"])

        log.info("Sequence complete!")

    def _safe_abort(self) -> None:
        """Best-effort cleanup after a failure. Tries to stop all actuators."""
        log.warning("Aborting — stopping all actuators")
        try:
            self._conveyor.stop()
        except Exception:
            pass
        try:
            self._extruder.stop_dispense()
        except Exception:
            pass
        try:
            self._conveyor.stop_zigzag()
        except Exception:
            pass
