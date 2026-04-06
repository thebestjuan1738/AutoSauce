"""
extruder.py — Extruder driver for the SauceBot.

Hardware:
    Motor controller signal  ◄── Pi GPIO 18  (hardware PWM via gpiozero/lgpio)
    Encoder A                ──► Pi GPIO 23
    Encoder B                ──► Pi GPIO 25
"""

import time
import threading

import RPi.GPIO as GPIO
from gpiozero import Servo

from pi.utils.logger import log
from pi.motion._shared import PWM_FREQ, _ESC_MIN_US, _ESC_MAX_US, _esc_value

# ─── GPIO pin assignments (BCM numbering) ─────────────────────────────────────
PIN_ESC       = 18    # PWM signal to goBILDA 1x20A controller
PIN_ENCODER_A = 23    # encoder channel A
PIN_ENCODER_B = 25    # encoder channel B

# ─── ESC pulse widths (microseconds) ──────────────────────────────────────────
_ESC_STOP        = 1500
_ESC_HOME_STRONG = 1300   # strong retract torque, homing phase 1
_ESC_HOME_SLOW   = 1450   # slow retract creep, homing phase 2
_ESC_DISPENSE    = 1650   # extend plunger
_ESC_RETRACT     = 1350   # retract plunger to zero

# ─── Motion constants ──────────────────────────────────────────────────────────
_TICKS_PER_REV         = 753
_DISPENSE_REVOLUTIONS  = 2.0
_DISPENSE_TARGET_TICKS = int(_DISPENSE_REVOLUTIONS * _TICKS_PER_REV)   # 1506
_HOME_PHASE1_TIMEOUT_S = 1.2    # max seconds waiting for movement in phase 1
_STALL_DETECT_MS       = 200    # ms with no encoder movement = stalled at limit
_MOTION_TIMEOUT_S      = 5.0    # max seconds for dispense/retract moves
_POLL_S                = 0.005  # 5 ms poll interval


class GPIOExtruder:
    """
    Controls the 5000 Series 12VDC extruder (sauce plunger) via a goBILDA 1x20A controller.

    Hardware:
        Motor controller signal  ◄── Pi GPIO 18  (hardware PWM via gpiozero/lgpio)
        Encoder A                ──► Pi GPIO 23
        Encoder B                ──► Pi GPIO 25

    Encoder-based position control:
        home()     — retracts to mechanical limit (two-phase), zeroes encoder
        dispense() — extends plunger to _DISPENSE_TARGET_TICKS
        retract()  — returns plunger to encoder zero (fully retracted)

    home() is called automatically in __init__. Call cleanup() on shutdown.
    """

    def __init__(self):
        self._ticks = 0
        self._lock  = threading.Lock()

        # Encoder via RPi.GPIO ISRs
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(PIN_ENCODER_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(PIN_ENCODER_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(PIN_ENCODER_A, GPIO.BOTH, callback=self._isr_a)
        GPIO.add_event_detect(PIN_ENCODER_B, GPIO.BOTH, callback=self._isr_b)

        # ESC via gpiozero Servo PWM
        self._esc = Servo(
            PIN_ESC,
            initial_value=0,
            min_pulse_width=_ESC_MIN_US / 1e6,
            max_pulse_width=_ESC_MAX_US / 1e6,
            frame_width=1 / PWM_FREQ,
        )

        self._set_esc(_ESC_STOP)
        log.info(
            "GPIOExtruder: ESC on GPIO %d, encoder on GPIO %d/%d",
            PIN_ESC, PIN_ENCODER_A, PIN_ENCODER_B,
        )
        log.info("GPIOExtruder: arming ESC (holding stop/neutral)...")
        self._set_esc(_ESC_STOP)
        time.sleep(2.0)
        log.info("GPIOExtruder: ESC armed")

        self.home()

    def cleanup(self) -> None:
        """Disarm ESC and release GPIO resources. Call on shutdown."""
        self._set_esc(_ESC_STOP)
        self._esc.value = 0
        self._esc.detach()
        self._esc.close()
        GPIO.cleanup([PIN_ENCODER_A, PIN_ENCODER_B])
        log.info("GPIOExtruder: cleanup done")

    # ─── Encoder ISRs ─────────────────────────────────────────────────────────

    def _isr_a(self, channel) -> None:
        a = GPIO.input(PIN_ENCODER_A)
        b = GPIO.input(PIN_ENCODER_B)
        with self._lock:
            if a == b:
                self._ticks += 1
            else:
                self._ticks -= 1

    def _isr_b(self, channel) -> None:
        a = GPIO.input(PIN_ENCODER_A)
        b = GPIO.input(PIN_ENCODER_B)
        with self._lock:
            if a != b:
                self._ticks += 1
            else:
                self._ticks -= 1

    def _get_ticks(self) -> int:
        with self._lock:
            return self._ticks

    def _zero_ticks(self) -> None:
        with self._lock:
            self._ticks = 0

    # ─── ESC control ──────────────────────────────────────────────────────────

    def _set_esc(self, pulse_us: int) -> None:
        self._esc.value = _esc_value(pulse_us)

    # ─── Motion ───────────────────────────────────────────────────────────────

    def home(self) -> None:
        """
        Phase 1: strong retract torque until movement is detected.
        Phase 2: slow retract creep until stall at mechanical retracted limit.
        Zeroes the encoder once stall is confirmed.
        """
        log.info("GPIOExtruder: homing — phase 1 (strong retract torque)...")
        last_ticks = self._get_ticks()

        start = time.time()
        self._set_esc(_ESC_HOME_STRONG)
        while True:
            if self._get_ticks() != last_ticks:
                break
            if time.time() - start > _HOME_PHASE1_TIMEOUT_S:
                self._set_esc(_ESC_STOP)
                raise RuntimeError(
                    "GPIOExtruder: no movement detected in homing phase 1 — "
                    "check ESC wiring, motor connection, and encoder"
                )
            time.sleep(_POLL_S)

        log.info("GPIOExtruder: homing — phase 2 (slow creep to retracted limit)...")
        last_ticks     = self._get_ticks()
        last_move_time = time.time()

        self._set_esc(_ESC_HOME_SLOW)
        while True:
            current = self._get_ticks()
            if current != last_ticks:
                last_ticks     = current
                last_move_time = time.time()

            if (time.time() - last_move_time) * 1000 > _STALL_DETECT_MS:
                self._set_esc(_ESC_STOP)
                time.sleep(0.2)
                self._zero_ticks()
                log.info("GPIOExtruder: homing complete — encoder zeroed")
                return

            time.sleep(_POLL_S)

    def dispense(self) -> None:
        """Extend plunger to _DISPENSE_TARGET_TICKS."""
        log.info(
            "GPIOExtruder: dispensing to %d ticks (current: %d)",
            _DISPENSE_TARGET_TICKS, self._get_ticks(),
        )
        start = time.time()
        self._set_esc(_ESC_DISPENSE)
        while self._get_ticks() < _DISPENSE_TARGET_TICKS:
            if time.time() - start > _MOTION_TIMEOUT_S:
                self._set_esc(_ESC_STOP)
                raise RuntimeError(
                    f"GPIOExtruder: dispense timed out after {_MOTION_TIMEOUT_S}s "
                    f"(ticks={self._get_ticks()}, target={_DISPENSE_TARGET_TICKS})"
                )
            time.sleep(_POLL_S)

        self._set_esc(_ESC_STOP)
        time.sleep(0.15)
        log.info("GPIOExtruder: dispense done (ticks=%d)", self._get_ticks())

    def retract(self) -> None:
        """Retract plunger back to encoder zero (fully retracted position)."""
        log.info("GPIOExtruder: retracting (current ticks: %d)", self._get_ticks())
        start = time.time()

        self._set_esc(_ESC_RETRACT)
        while self._get_ticks() > 0:
            if time.time() - start > _MOTION_TIMEOUT_S:
                self._set_esc(_ESC_STOP)
                raise RuntimeError(
                    f"GPIOExtruder: retract timed out after {_MOTION_TIMEOUT_S}s "
                    f"(ticks={self._get_ticks()}, target=0)"
                )
            time.sleep(_POLL_S)

        self._set_esc(_ESC_STOP)
        time.sleep(0.15)
        log.info("GPIOExtruder: retract done (ticks=%d)", self._get_ticks())
