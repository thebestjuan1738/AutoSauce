"""
_shared.py — Shared constants and helpers used across GPIO motion drivers.
"""

from gpiozero import Device
from gpiozero.pins.lgpio import LGPIOFactory

# Use lgpio pin factory — required for Raspberry Pi 5
Device.pin_factory = LGPIOFactory()

# ─── PWM ──────────────────────────────────────────────────────────────────────
PWM_FREQ     = 50      # Hz — standard RC servo/ESC signal

_ESC_MIN_US  = 1100
_ESC_MAX_US  = 1900

# Duty cycle values (%) corresponding to the specification:
#   1100 µs = 5.5% @ 50 Hz  →  full reverse
#   1500 µs = 7.5% @ 50 Hz  →  stop / neutral
#   1900 µs = 9.5% @ 50 Hz  →  full forward
DUTY_STOP     = 7.5
DUTY_FULL_FWD = 9.5
DUTY_FULL_REV = 5.5


def _esc_value(pulse_us: int) -> float:
    """
    Convert an ESC pulse width (µs) to a gpiozero Servo value (-1.0 to 1.0).
        1100 µs → -1.0  (full reverse)
        1500 µs →  0.0  (stop / neutral)
        1900 µs → +1.0  (full forward)
    """
    return (pulse_us - 1500) / 400.0


def _speed_to_duty(speed: int) -> float:
    """
    Map an abstract speed (0–100) to a PWM duty cycle percentage.
        speed 0   → 7.5% (stop/neutral)
        speed 100 → 9.5% (full forward)
    """
    speed = max(0, min(100, speed))
    return DUTY_STOP + (speed / 100.0) * (DUTY_FULL_FWD - DUTY_STOP)
