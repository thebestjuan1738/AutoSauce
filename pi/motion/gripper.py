"""
gripper.py — Gripper driver for the SauceBot.

Hardware:
    Motor controller signal  ◄── Pi GPIO 12  (hardware PWM via gpiozero/lgpio)
    Encoder A                ──► Pi GPIO 16
    Encoder B                ──► Pi GPIO 20
"""

import time
import threading

import lgpio
from gpiozero import Servo

from pi.utils.logger import log
from pi.motion._shared import PWM_FREQ, _ESC_MIN_US, _ESC_MAX_US, _esc_value

# ─── GPIO chip (BCM numbering) ───────────────────────────────────────────────
# Pi 5 uses gpiochip4 for the 40-pin header; Pi 4 uses gpiochip0
_GPIOCHIP = 4

# ─── GPIO pin assignments (BCM numbering) ─────────────────────────────────────
PIN_ESC       = 12    # PWM signal to goBILDA 1x20A controller
PIN_ENCODER_A = 16    # encoder channel A
PIN_ENCODER_B = 20    # encoder channel B

# ─── ESC pulse widths (microseconds) ──────────────────────────────────────────
_ESC_STOP        = 1500
_ESC_OPEN_STRONG = 1700   # strong torque, homing phase 1
_ESC_OPEN_SLOW   = 1550   # slow creep, homing phase 2
_ESC_OPEN_FAST   = 1650   # fast return to zero
_ESC_CLOSE_FAST  = 1350   # fast close to target

# ─── Motion constants ──────────────────────────────────────────────────────────
_TICKS_PER_REV         = 753
_CLOSE_REVOLUTIONS     = 1.6
_CLOSE_TARGET_TICKS    = -int(_CLOSE_REVOLUTIONS * _TICKS_PER_REV)   # -1204
_HOME_PHASE1_TIMEOUT_S = 1.2    # max seconds waiting for movement in phase 1
_STALL_DETECT_MS       = 200    # ms with no encoder movement = stalled at limit
_MOTION_TIMEOUT_S      = 5.0    # max seconds for open/close moves
_POLL_S                = 0.005  # 5 ms poll interval


class GPIOGripper:
    """
    Controls the 5000 Series 12VDC gripper motor via a goBILDA 1x20A controller.

    Hardware:
        Motor controller signal  ◄── Pi GPIO 12  (hardware PWM via gpiozero/lgpio)
        Encoder A                ──► Pi GPIO 16
        Encoder B                ──► Pi GPIO 20

    Encoder-based position control:
        home()  — drives to mechanical open limit (two-phase), zeroes encoder
        close() — drives to _CLOSE_TARGET_TICKS (-1204 ticks)
        open()  — returns to encoder zero (open position)

    home() is called automatically in __init__. Call cleanup() on shutdown.
    """

    def __init__(self):
        self._ticks = 0
        self._lock  = threading.Lock()

        # Encoder via lgpio ISRs (Pi 5 compatible)
        self._h = lgpio.gpiochip_open(_GPIOCHIP)
        lgpio.gpio_claim_input(self._h, PIN_ENCODER_A, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_input(self._h, PIN_ENCODER_B, lgpio.SET_PULL_UP)
        self._cb_a = lgpio.callback(self._h, PIN_ENCODER_A, lgpio.BOTH_EDGES, self._isr_a)
        self._cb_b = lgpio.callback(self._h, PIN_ENCODER_B, lgpio.BOTH_EDGES, self._isr_b)

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
            "GPIOGripper: ESC on GPIO %d, encoder on GPIO %d/%d",
            PIN_ESC, PIN_ENCODER_A, PIN_ENCODER_B,
        )
        log.info("GPIOGripper: waiting 2s for ESC to arm...")
        time.sleep(2.0)

        self.home()

    def cleanup(self) -> None:
        """Disarm ESC and release GPIO resources. Call on shutdown."""
        self._set_esc(_ESC_STOP)
        self._esc.value = 0
        self._esc.detach()
        self._esc.close()
        self._cb_a.cancel()
        self._cb_b.cancel()
        lgpio.gpiochip_close(self._h)
        log.info("GPIOGripper: cleanup done")

    # ─── Encoder ISRs ─────────────────────────────────────────────────────────

    def _isr_a(self, chip, gpio, level, tick) -> None:
        a = lgpio.gpio_read(self._h, PIN_ENCODER_A)
        b = lgpio.gpio_read(self._h, PIN_ENCODER_B)
        with self._lock:
            if a == b:
                self._ticks += 1
            else:
                self._ticks -= 1

    def _isr_b(self, chip, gpio, level, tick) -> None:
        a = lgpio.gpio_read(self._h, PIN_ENCODER_A)
        b = lgpio.gpio_read(self._h, PIN_ENCODER_B)
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
        Pre-move: nudge closed briefly to clear the mechanical open stop.
        Phase 1: strong open torque until movement is detected.
        Phase 2: slow creep until stall at mechanical open limit.
        Zeroes the encoder once stall is confirmed.
        """
        log.info("GPIOGripper: homing — pre-close nudge (clearing open stop)...")
        self._set_esc(_ESC_CLOSE_FAST)
        time.sleep(0.3)
        self._set_esc(_ESC_STOP)
        time.sleep(0.1)

        log.info("GPIOGripper: homing — phase 1 (strong open torque)...")
        last_ticks = self._get_ticks()

        start = time.time()
        while True:
            self._set_esc(_ESC_OPEN_STRONG)
            if self._get_ticks() != last_ticks:
                break
            if time.time() - start > _HOME_PHASE1_TIMEOUT_S:
                self._set_esc(_ESC_STOP)
                raise RuntimeError(
                    "GPIOGripper: no movement detected in homing phase 1 — "
                    "check ESC wiring, motor connection, and encoder"
                )
            time.sleep(_POLL_S)

        log.info("GPIOGripper: homing — phase 2 (slow creep to limit)...")
        last_ticks     = self._get_ticks()
        last_move_time = time.time()

        while True:
            self._set_esc(_ESC_OPEN_SLOW)
            current = self._get_ticks()
            if current != last_ticks:
                last_ticks     = current
                last_move_time = time.time()

            if (time.time() - last_move_time) * 1000 > _STALL_DETECT_MS:
                self._set_esc(_ESC_STOP)
                time.sleep(0.2)
                self._zero_ticks()
                log.info("GPIOGripper: homing complete — encoder zeroed")
                return

            time.sleep(_POLL_S)

    def open(self) -> None:
        """Fast return to encoder zero (open position)."""
        log.info("GPIOGripper: opening (current ticks: %d)", self._get_ticks())
        start = time.time()

        while self._get_ticks() < 0:
            self._set_esc(_ESC_OPEN_FAST)
            if time.time() - start > _MOTION_TIMEOUT_S:
                self._set_esc(_ESC_STOP)
                raise RuntimeError(
                    f"GPIOGripper: open timed out after {_MOTION_TIMEOUT_S}s "
                    f"(ticks={self._get_ticks()}, target=0)"
                )
            time.sleep(_POLL_S)

        self._set_esc(_ESC_STOP)
        time.sleep(0.15)
        log.info("GPIOGripper: open done (ticks=%d)", self._get_ticks())

    def close(self) -> None:
        """Fast close to _CLOSE_TARGET_TICKS (1.6 revolutions)."""
        log.info(
            "GPIOGripper: closing to %d ticks (current: %d)",
            _CLOSE_TARGET_TICKS, self._get_ticks(),
        )
        start = time.time()

        while self._get_ticks() > _CLOSE_TARGET_TICKS:
            self._set_esc(_ESC_CLOSE_FAST)
            if time.time() - start > _MOTION_TIMEOUT_S:
                self._set_esc(_ESC_STOP)
                raise RuntimeError(
                    f"GPIOGripper: close timed out after {_MOTION_TIMEOUT_S}s "
                    f"(ticks={self._get_ticks()}, target={_CLOSE_TARGET_TICKS})"
                )
            time.sleep(_POLL_S)

        self._set_esc(_ESC_STOP)
        time.sleep(0.15)
        log.info("GPIOGripper: close done (ticks=%d)", self._get_ticks())
