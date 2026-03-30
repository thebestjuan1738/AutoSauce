"""
gpio_drivers.py

Real hardware drivers for the SauceBot.
Mirrors the interface of mock_drivers.py exactly — swap in main.py by setting USE_MOCK = False.

Dependencies (Pi only):
    pip install pyvesc pyserial RPi.GPIO --break-system-packages

Hardware:
    Gantry   — Turnigy SK8 V2 VESC over UART (PyVESC), closed-loop encoder position
    Extruder — goBILDA 3105 PWM (GPIO 18)
    Gripper  — goBILDA 3105 PWM (GPIO 23)
    Conveyor — goBILDA 3105 PWM (GPIO 24)
"""

import time
import pyvesc
from pyvesc import VESC
import RPi.GPIO as GPIO

from pi.utils.logger import log

# ─── Gantry / VESC ────────────────────────────────────────────────────────────
SERIAL_PORT  = "/dev/ttyAMA0"
BAUD_RATE    = 115200

# TODO: tune TRAVEL_RPM once motor detection is done in VESC Tool
TRAVEL_RPM   = 3000

# TODO: calibrate from (encoder PPR × gear/pulley ratio) after VESC Tool setup
# e.g.  TICKS_PER_MM = encoder_PPR * motor_turns_per_mm
TICKS_PER_MM = 100

# How close (in ticks) counts as "arrived"
POSITION_TOLERANCE_TICKS = 50

# Raise TimeoutError if the gantry doesn't reach the target within this many seconds
TRAVEL_TIMEOUT_S = 30

# ─── PWM general ──────────────────────────────────────────────────────────────
PWM_FREQ     = 50    # Hz — standard RC servo/ESC signal

# Duty cycle values (%) corresponding to the specification:
#   1100 µs = 5.5% @ 50 Hz  →  full reverse
#   1500 µs = 7.5% @ 50 Hz  →  stop / neutral
#   1900 µs = 9.5% @ 50 Hz  →  full forward
DUTY_STOP     = 7.5
DUTY_FULL_FWD = 9.5
DUTY_FULL_REV = 5.5

# ─── GPIO pin assignments (BCM numbering) ─────────────────────────────────────
# TODO: confirm all three pins match actual wiring before powering on
PIN_EXTRUDER = 18
PIN_GRIPPER  = 23
PIN_CONVEYOR = 24


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _speed_to_duty(speed: int) -> float:
    """
    Map an abstract speed (0–100) to a PWM duty cycle.
        speed 0   → 7.5% (stop/neutral)
        speed 100 → 9.5% (full forward)
    """
    speed = max(0, min(100, speed))
    return DUTY_STOP + (speed / 100.0) * (DUTY_FULL_FWD - DUTY_STOP)


# ─── Gantry ───────────────────────────────────────────────────────────────────

class GPIOGantry:
    """
    Controls the brushless DC gantry motor via the Turnigy SK8 V2 VESC over UART.

    Requires:
      - VESC Tool motor detection + encoder setup done once before first use
      - UART enabled on the Pi (raspi-config → Interface Options → Serial)
      - TICKS_PER_MM calibrated to actual belt/pulley ratio
    """

    def __init__(self):
        log.info("GPIOGantry: connecting to VESC on %s @ %d baud", SERIAL_PORT, BAUD_RATE)
        self._vesc = VESC(serial_port=SERIAL_PORT, baudrate=BAUD_RATE)
        # Assumes the gantry is physically at the home position when the program starts.
        # If you need a homing routine, add it here.
        self._position_mm = 500
        log.info("GPIOGantry: connected")

    def move_to(self, position_mm: int) -> None:
        """
        Command the gantry to travel to position_mm (from the dock end of the rail).
        Blocks until the encoder confirms the position is reached or raises TimeoutError.
        """
        if position_mm == self._position_mm:
            return

        delta_mm    = position_mm - self._position_mm
        delta_ticks = int(abs(delta_mm) * TICKS_PER_MM)
        direction   = 1 if delta_mm > 0 else -1

        start_ticks = self._get_encoder_position()
        target_ticks = start_ticks + direction * delta_ticks

        log.info(
            "GPIOGantry: %dmm → %dmm  (Δ%d ticks, RPM=%d)",
            self._position_mm, position_mm, direction * delta_ticks, TRAVEL_RPM,
        )

        self._vesc.set_rpm(direction * TRAVEL_RPM)

        deadline = time.monotonic() + TRAVEL_TIMEOUT_S
        while True:
            current_ticks = self._get_encoder_position()
            ticks_remaining = abs(target_ticks - current_ticks)

            if ticks_remaining <= POSITION_TOLERANCE_TICKS:
                break

            if time.monotonic() > deadline:
                self._vesc.set_rpm(0)
                raise TimeoutError(
                    f"Gantry timed out after {TRAVEL_TIMEOUT_S}s "
                    f"moving to {position_mm}mm "
                    f"(~{ticks_remaining} ticks remaining)"
                )

            time.sleep(0.05)  # poll at 20 Hz

        self._vesc.set_rpm(0)
        self._position_mm = position_mm
        log.info("GPIOGantry: arrived at %dmm", position_mm)

    def _get_encoder_position(self) -> int:
        """Return the absolute encoder tick count from the VESC."""
        measurements = self._vesc.get_measurements()
        return measurements.tachometer_abs


# ─── Extruder ─────────────────────────────────────────────────────────────────

class GPIOExtruder:
    """
    Controls the brushed linear extruder (sauce plunger) via PWM on GPIO 18.
    """

    def __init__(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(PIN_EXTRUDER, GPIO.OUT)
        self._pwm = GPIO.PWM(PIN_EXTRUDER, PWM_FREQ)
        self._pwm.start(DUTY_STOP)

        # ── Arming sequence (uncomment if the goBILDA controller requires it) ──
        # Some ESCs need a brief full-forward + full-reverse pulse on power-up
        # before they will accept normal commands.
        #
        # log.info("GPIOExtruder: arming ESC...")
        # self._pwm.ChangeDutyCycle(DUTY_FULL_FWD)
        # time.sleep(2.0)
        # self._pwm.ChangeDutyCycle(DUTY_FULL_REV)
        # time.sleep(2.0)
        # self._pwm.ChangeDutyCycle(DUTY_STOP)
        # time.sleep(1.0)
        # log.info("GPIOExtruder: armed")

        log.info("GPIOExtruder: PWM started on GPIO %d", PIN_EXTRUDER)

    def dispense(self, duration_ms: int) -> None:
        """Run the extruder plunger forward for duration_ms, then stop."""
        log.info("GPIOExtruder: dispensing for %dms", duration_ms)
        self._pwm.ChangeDutyCycle(DUTY_FULL_FWD)
        time.sleep(duration_ms / 1000.0)
        self._pwm.ChangeDutyCycle(DUTY_STOP)
        log.info("GPIOExtruder: stopped")


# ─── Gripper ──────────────────────────────────────────────────────────────────

class GPIOGripper:
    """
    Controls the brushed DC gripper motor via PWM on GPIO 23.
    Forward = close (grab bottle), reverse = open (release bottle).
    """

    def __init__(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(PIN_GRIPPER, GPIO.OUT)
        self._pwm = GPIO.PWM(PIN_GRIPPER, PWM_FREQ)
        self._pwm.start(DUTY_STOP)

        # ── Arming sequence (uncomment if the goBILDA controller requires it) ──
        #
        # log.info("GPIOGripper: arming ESC...")
        # self._pwm.ChangeDutyCycle(DUTY_FULL_FWD)
        # time.sleep(2.0)
        # self._pwm.ChangeDutyCycle(DUTY_FULL_REV)
        # time.sleep(2.0)
        # self._pwm.ChangeDutyCycle(DUTY_STOP)
        # time.sleep(1.0)
        # log.info("GPIOGripper: armed")

        log.info("GPIOGripper: PWM started on GPIO %d", PIN_GRIPPER)

    def close(self, duration_ms: int) -> None:
        """Drive gripper forward (close) for duration_ms, then stop."""
        log.info("GPIOGripper: closing (%dms)", duration_ms)
        self._pwm.ChangeDutyCycle(DUTY_FULL_FWD)
        time.sleep(duration_ms / 1000.0)
        self._pwm.ChangeDutyCycle(DUTY_STOP)

    def open(self, duration_ms: int) -> None:
        """Drive gripper in reverse (open) for duration_ms, then stop."""
        log.info("GPIOGripper: opening (%dms)", duration_ms)
        self._pwm.ChangeDutyCycle(DUTY_FULL_REV)
        time.sleep(duration_ms / 1000.0)
        self._pwm.ChangeDutyCycle(DUTY_STOP)


# ─── Conveyor ─────────────────────────────────────────────────────────────────

class GPIOConveyor:
    """
    Controls the brushed DC conveyor belt motor via PWM on GPIO 24.
    Speed is an abstract 0–100 value; see _speed_to_duty() for the mapping.
    """

    def __init__(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(PIN_CONVEYOR, GPIO.OUT)
        self._pwm = GPIO.PWM(PIN_CONVEYOR, PWM_FREQ)
        self._pwm.start(DUTY_STOP)

        # ── Arming sequence (uncomment if the goBILDA controller requires it) ──
        #
        # log.info("GPIOConveyor: arming ESC...")
        # self._pwm.ChangeDutyCycle(DUTY_FULL_FWD)
        # time.sleep(2.0)
        # self._pwm.ChangeDutyCycle(DUTY_FULL_REV)
        # time.sleep(2.0)
        # self._pwm.ChangeDutyCycle(DUTY_STOP)
        # time.sleep(1.0)
        # log.info("GPIOConveyor: armed")

        log.info("GPIOConveyor: PWM started on GPIO %d", PIN_CONVEYOR)

    def start(self, speed: int) -> None:
        """Set conveyor belt to the given speed (0–100)."""
        duty = _speed_to_duty(speed)
        log.info("GPIOConveyor: starting at speed %d (duty %.2f%%)", speed, duty)
        self._pwm.ChangeDutyCycle(duty)

    def stop(self) -> None:
        """Stop the conveyor belt."""
        log.info("GPIOConveyor: stopped")
        self._pwm.ChangeDutyCycle(DUTY_STOP)
