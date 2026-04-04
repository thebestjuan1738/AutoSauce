"""
conveyor.py — Conveyor belt driver for the SauceBot.

Hardware:
    goBILDA 3105 brushed DC motor via PWM on GPIO 24.
"""

from gpiozero import Servo

from pi.utils.logger import log
from pi.motion._shared import PWM_FREQ, _ESC_MIN_US, _ESC_MAX_US, _esc_value, _speed_to_duty

# ─── GPIO pin assignments (BCM numbering) ─────────────────────────────────────
PIN_CONVEYOR = 24


class GPIOConveyor:
    """
    Controls the brushed DC conveyor belt motor via PWM on GPIO 24.
    Speed is an abstract 0–100 value; see _speed_to_duty() for the mapping.
    """

    def __init__(self):
        self._servo = Servo(
            PIN_CONVEYOR,
            initial_value=0,
            min_pulse_width=_ESC_MIN_US / 1e6,
            max_pulse_width=_ESC_MAX_US / 1e6,
            frame_width=1 / PWM_FREQ,
        )

        # ── Arming sequence (uncomment if the goBILDA controller requires it) ──
        # import time
        # log.info("GPIOConveyor: arming ESC...")
        # self._servo.value = _esc_value(int(DUTY_FULL_FWD * 200))   # full fwd
        # time.sleep(2.0)
        # self._servo.value = _esc_value(int(DUTY_FULL_REV * 200))   # full rev
        # time.sleep(2.0)
        # self._servo.value = 0                                       # stop
        # time.sleep(1.0)
        # log.info("GPIOConveyor: armed")

        log.info("GPIOConveyor: servo PWM started on GPIO %d", PIN_CONVEYOR)

    def start(self, speed: int) -> None:
        """Set conveyor belt to the given speed (0–100)."""
        duty_pct = _speed_to_duty(speed)
        # duty% at 50 Hz → pulse width: duty% × 200 = pulse µs
        val = _esc_value(int(duty_pct * 200))
        log.info("GPIOConveyor: starting at speed %d (servo value %.4f)", speed, val)
        self._servo.value = val

    def stop(self) -> None:
        """Stop the conveyor belt."""
        self._servo.value = 0
        log.info("GPIOConveyor: stopped")
