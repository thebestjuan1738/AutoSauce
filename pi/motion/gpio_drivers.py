"""
gpio_drivers.py

Real hardware drivers for the SauceBot.
Mirrors the interface of mock_drivers.py exactly — swap in main.py by setting USE_MOCK = False.

Dependencies (Pi only):
    pip install pyvesc pyserial RPi.GPIO pigpio --break-system-packages
    sudo systemctl enable pigpiod && sudo systemctl start pigpiod

Hardware:
    Gantry   — Turnigy SK8 V2 VESC over UART (PyVESC), closed-loop encoder position
    Extruder — 5000 Series 12VDC motor + goBILDA 1x20A controller
                 ESC signal ◄── GPIO 18 (hardware PWM via pigpio)
                 Encoder A  ──► GPIO 23
                 Encoder B  ──► GPIO 25
    Gripper  — 5000 Series 12VDC motor + goBILDA 1x20A controller
                 ESC signal ◄── GPIO 12 (hardware PWM via pigpio)
                 Encoder A  ──► GPIO 16
                 Encoder B  ──► GPIO 20
    Conveyor — goBILDA 3105 PWM (GPIO 24)
"""

import threading
import time
import pigpio
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
# TODO: confirm all pins match actual wiring before powering on
PIN_CONVEYOR = 24

# Extruder — 5000 Series 12VDC motor + goBILDA 1x20A controller
PIN_EXTRUDER_ESC       = 18    # PWM signal to goBILDA 1x20A controller (hardware PWM via pigpio)
PIN_EXTRUDER_ENCODER_A = 23    # encoder channel A
PIN_EXTRUDER_ENCODER_B = 25    # encoder channel B

# ─── Extruder ESC pulse widths (microseconds) ─────────────────────────────────
_EXTRUDER_ESC_STOP         = 1500
_EXTRUDER_ESC_HOME_STRONG  = 1300   # strong retract torque, homing phase 1
_EXTRUDER_ESC_HOME_SLOW    = 1450   # slow retract creep, homing phase 2
_EXTRUDER_ESC_DISPENSE     = 1650   # extend plunger
_EXTRUDER_ESC_RETRACT      = 1350   # retract plunger to zero

# ─── Extruder motion constants ────────────────────────────────────────────────
_EXTRUDER_TICKS_PER_REV         = 753
_EXTRUDER_DISPENSE_REVOLUTIONS  = 2.0
_EXTRUDER_DISPENSE_TARGET_TICKS = int(_EXTRUDER_DISPENSE_REVOLUTIONS * _EXTRUDER_TICKS_PER_REV)  # 1506
_EXTRUDER_HOME_PHASE1_TIMEOUT_S = 1.2    # max seconds waiting for movement in phase 1
_EXTRUDER_STALL_DETECT_MS       = 200    # ms with no encoder movement = stalled at limit
_EXTRUDER_MOTION_TIMEOUT_S      = 5.0    # max seconds for dispense/retract moves
_EXTRUDER_POLL_S                = 0.005  # 5 ms poll interval

# Gripper — 5000 Series 12VDC motor + goBILDA 1x20A controller
PIN_GRIPPER_ESC       = 12    # PWM signal to goBILDA 1x20A controller (hardware PWM via pigpio)
PIN_GRIPPER_ENCODER_A = 16    # encoder channel A
PIN_GRIPPER_ENCODER_B = 20    # encoder channel B

# ─── Gripper ESC pulse widths (microseconds) ──────────────────────────────────
_GRIPPER_ESC_STOP         = 1500
_GRIPPER_ESC_OPEN_STRONG  = 1700   # strong torque, homing phase 1
_GRIPPER_ESC_OPEN_SLOW    = 1550   # slow creep, homing phase 2
_GRIPPER_ESC_OPEN_FAST    = 1650   # fast return to zero
_GRIPPER_ESC_CLOSE_FAST   = 1350   # fast close to target

# ─── Gripper motion constants ─────────────────────────────────────────────────
_GRIPPER_TICKS_PER_REV         = 753
_GRIPPER_CLOSE_REVOLUTIONS     = 1.6
_GRIPPER_CLOSE_TARGET_TICKS    = -int(_GRIPPER_CLOSE_REVOLUTIONS * _GRIPPER_TICKS_PER_REV)  # -1204
_GRIPPER_HOME_PHASE1_TIMEOUT_S = 1.2    # max seconds waiting for movement in phase 1
_GRIPPER_STALL_DETECT_MS       = 200    # ms with no encoder movement = stalled at limit
_GRIPPER_MOTION_TIMEOUT_S      = 5.0    # max seconds for open/close moves
_GRIPPER_POLL_S                = 0.005  # 5 ms poll interval


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
    Controls the 5000 Series 12VDC extruder (sauce plunger) via a goBILDA 1x20A controller.

    Hardware:
        Motor controller signal  ◄── Pi GPIO 18  (hardware PWM via pigpio)
        Encoder A                ──► Pi GPIO 23
        Encoder B                ──► Pi GPIO 25

    Encoder-based position control:
        home()     — retracts to mechanical limit (two-phase), zeroes encoder
        dispense() — extends plunger to _EXTRUDER_DISPENSE_TARGET_TICKS
        retract()  — returns plunger to encoder zero (fully retracted)

    home() is called automatically in __init__. Call cleanup() on shutdown.

    Requires pigpiod running:
        sudo systemctl start pigpiod
    """

    def __init__(self):
        self._ticks = 0
        self._lock  = threading.Lock()

        # Encoder via RPi.GPIO ISRs
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(PIN_EXTRUDER_ENCODER_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(PIN_EXTRUDER_ENCODER_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(PIN_EXTRUDER_ENCODER_A, GPIO.BOTH, callback=self._isr_a)
        GPIO.add_event_detect(PIN_EXTRUDER_ENCODER_B, GPIO.BOTH, callback=self._isr_b)

        # ESC via pigpio hardware PWM
        self._pi = pigpio.pi()
        if not self._pi.connected:
            raise RuntimeError(
                "GPIOExtruder: pigpio daemon not running — "
                "run: sudo systemctl start pigpiod"
            )

        self._set_esc(_EXTRUDER_ESC_STOP)
        log.info(
            "GPIOExtruder: ESC on GPIO %d, encoder on GPIO %d/%d",
            PIN_EXTRUDER_ESC, PIN_EXTRUDER_ENCODER_A, PIN_EXTRUDER_ENCODER_B,
        )
        log.info("GPIOExtruder: waiting 2s for ESC to arm...")
        time.sleep(2.0)

        self.home()

    def cleanup(self) -> None:
        """Disarm ESC and release GPIO resources. Call on shutdown."""
        self._set_esc(_EXTRUDER_ESC_STOP)
        self._pi.set_servo_pulsewidth(PIN_EXTRUDER_ESC, 0)
        self._pi.stop()
        GPIO.cleanup([PIN_EXTRUDER_ENCODER_A, PIN_EXTRUDER_ENCODER_B])
        log.info("GPIOExtruder: cleanup done")

    # ─── Encoder ISRs ─────────────────────────────────────────────────────────

    def _isr_a(self, channel) -> None:
        a = GPIO.input(PIN_EXTRUDER_ENCODER_A)
        b = GPIO.input(PIN_EXTRUDER_ENCODER_B)
        with self._lock:
            if a == b:
                self._ticks += 1
            else:
                self._ticks -= 1

    def _isr_b(self, channel) -> None:
        a = GPIO.input(PIN_EXTRUDER_ENCODER_A)
        b = GPIO.input(PIN_EXTRUDER_ENCODER_B)
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
        self._pi.set_servo_pulsewidth(PIN_EXTRUDER_ESC, pulse_us)

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
        while True:
            self._set_esc(_EXTRUDER_ESC_HOME_STRONG)
            if self._get_ticks() != last_ticks:
                break
            if time.time() - start > _EXTRUDER_HOME_PHASE1_TIMEOUT_S:
                self._set_esc(_EXTRUDER_ESC_STOP)
                raise RuntimeError(
                    "GPIOExtruder: no movement detected in homing phase 1 — "
                    "check ESC wiring, motor connection, and encoder"
                )
            time.sleep(_EXTRUDER_POLL_S)

        log.info("GPIOExtruder: homing — phase 2 (slow creep to retracted limit)...")
        last_ticks     = self._get_ticks()
        last_move_time = time.time()

        while True:
            self._set_esc(_EXTRUDER_ESC_HOME_SLOW)
            current = self._get_ticks()
            if current != last_ticks:
                last_ticks     = current
                last_move_time = time.time()

            if (time.time() - last_move_time) * 1000 > _EXTRUDER_STALL_DETECT_MS:
                self._set_esc(_EXTRUDER_ESC_STOP)
                time.sleep(0.2)
                self._zero_ticks()
                log.info("GPIOExtruder: homing complete — encoder zeroed")
                return

            time.sleep(_EXTRUDER_POLL_S)

    def dispense(self) -> None:
        """Extend plunger to _EXTRUDER_DISPENSE_TARGET_TICKS."""
        log.info(
            "GPIOExtruder: dispensing to %d ticks (current: %d)",
            _EXTRUDER_DISPENSE_TARGET_TICKS, self._get_ticks(),
        )
        start = time.time()

        while self._get_ticks() < _EXTRUDER_DISPENSE_TARGET_TICKS:
            self._set_esc(_EXTRUDER_ESC_DISPENSE)
            if time.time() - start > _EXTRUDER_MOTION_TIMEOUT_S:
                self._set_esc(_EXTRUDER_ESC_STOP)
                raise RuntimeError(
                    f"GPIOExtruder: dispense timed out after {_EXTRUDER_MOTION_TIMEOUT_S}s "
                    f"(ticks={self._get_ticks()}, target={_EXTRUDER_DISPENSE_TARGET_TICKS})"
                )
            time.sleep(_EXTRUDER_POLL_S)

        self._set_esc(_EXTRUDER_ESC_STOP)
        time.sleep(0.15)
        log.info("GPIOExtruder: dispense done (ticks=%d)", self._get_ticks())

    def retract(self) -> None:
        """Retract plunger back to encoder zero (fully retracted position)."""
        log.info("GPIOExtruder: retracting (current ticks: %d)", self._get_ticks())
        start = time.time()

        while self._get_ticks() > 0:
            self._set_esc(_EXTRUDER_ESC_RETRACT)
            if time.time() - start > _EXTRUDER_MOTION_TIMEOUT_S:
                self._set_esc(_EXTRUDER_ESC_STOP)
                raise RuntimeError(
                    f"GPIOExtruder: retract timed out after {_EXTRUDER_MOTION_TIMEOUT_S}s "
                    f"(ticks={self._get_ticks()}, target=0)"
                )
            time.sleep(_EXTRUDER_POLL_S)

        self._set_esc(_EXTRUDER_ESC_STOP)
        time.sleep(0.15)
        log.info("GPIOExtruder: retract done (ticks=%d)", self._get_ticks())


# ─── Gripper ──────────────────────────────────────────────────────────────────

class GPIOGripper:
    """
    Controls the 5000 Series 12VDC gripper motor via a goBILDA 1x20A controller.

    Hardware:
        Motor controller signal  ◄── Pi GPIO 12  (hardware PWM via pigpio)
        Encoder A                ──► Pi GPIO 16
        Encoder B                ──► Pi GPIO 20

    Encoder-based position control:
        home()  — drives to mechanical open limit (two-phase), zeroes encoder
        close() — drives to _GRIPPER_CLOSE_TARGET_TICKS (-1204 ticks)
        open()  — returns to encoder zero (open position)

    home() is called automatically in __init__. Call cleanup() on shutdown.

    Requires pigpiod running:
        sudo systemctl start pigpiod
    """

    def __init__(self):
        self._ticks = 0
        self._lock  = threading.Lock()

        # Encoder via RPi.GPIO ISRs
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(PIN_GRIPPER_ENCODER_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(PIN_GRIPPER_ENCODER_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(PIN_GRIPPER_ENCODER_A, GPIO.BOTH, callback=self._isr_a)
        GPIO.add_event_detect(PIN_GRIPPER_ENCODER_B, GPIO.BOTH, callback=self._isr_b)

        # ESC via pigpio hardware PWM
        self._pi = pigpio.pi()
        if not self._pi.connected:
            raise RuntimeError(
                "GPIOGripper: pigpio daemon not running — "
                "run: sudo systemctl start pigpiod"
            )

        self._set_esc(_GRIPPER_ESC_STOP)
        log.info(
            "GPIOGripper: ESC on GPIO %d, encoder on GPIO %d/%d",
            PIN_GRIPPER_ESC, PIN_GRIPPER_ENCODER_A, PIN_GRIPPER_ENCODER_B,
        )
        log.info("GPIOGripper: waiting 2s for ESC to arm...")
        time.sleep(2.0)

        self.home()

    def cleanup(self) -> None:
        """Disarm ESC and release GPIO resources. Call on shutdown."""
        self._set_esc(_GRIPPER_ESC_STOP)
        self._pi.set_servo_pulsewidth(PIN_GRIPPER_ESC, 0)
        self._pi.stop()
        GPIO.cleanup([PIN_GRIPPER_ENCODER_A, PIN_GRIPPER_ENCODER_B])
        log.info("GPIOGripper: cleanup done")

    # ─── Encoder ISRs ─────────────────────────────────────────────────────────

    def _isr_a(self, channel) -> None:
        a = GPIO.input(PIN_GRIPPER_ENCODER_A)
        b = GPIO.input(PIN_GRIPPER_ENCODER_B)
        with self._lock:
            if a == b:
                self._ticks += 1
            else:
                self._ticks -= 1

    def _isr_b(self, channel) -> None:
        a = GPIO.input(PIN_GRIPPER_ENCODER_A)
        b = GPIO.input(PIN_GRIPPER_ENCODER_B)
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
        self._pi.set_servo_pulsewidth(PIN_GRIPPER_ESC, pulse_us)

    # ─── Motion ───────────────────────────────────────────────────────────────

    def home(self) -> None:
        """
        Phase 1: strong open torque until movement is detected.
        Phase 2: slow creep until stall at mechanical open limit.
        Zeroes the encoder once stall is confirmed.
        """
        log.info("GPIOGripper: homing — phase 1 (strong open torque)...")
        last_ticks = self._get_ticks()

        start = time.time()
        while True:
            self._set_esc(_GRIPPER_ESC_OPEN_STRONG)
            if self._get_ticks() != last_ticks:
                break
            if time.time() - start > _GRIPPER_HOME_PHASE1_TIMEOUT_S:
                self._set_esc(_GRIPPER_ESC_STOP)
                raise RuntimeError(
                    "GPIOGripper: no movement detected in homing phase 1 — "
                    "check ESC wiring, motor connection, and encoder"
                )
            time.sleep(_GRIPPER_POLL_S)

        log.info("GPIOGripper: homing — phase 2 (slow creep to limit)...")
        last_ticks     = self._get_ticks()
        last_move_time = time.time()

        while True:
            self._set_esc(_GRIPPER_ESC_OPEN_SLOW)
            current = self._get_ticks()
            if current != last_ticks:
                last_ticks     = current
                last_move_time = time.time()

            if (time.time() - last_move_time) * 1000 > _GRIPPER_STALL_DETECT_MS:
                self._set_esc(_GRIPPER_ESC_STOP)
                time.sleep(0.2)
                self._zero_ticks()
                log.info("GPIOGripper: homing complete — encoder zeroed")
                return

            time.sleep(_GRIPPER_POLL_S)

    def open(self) -> None:
        """Fast return to encoder zero (open position)."""
        log.info("GPIOGripper: opening (current ticks: %d)", self._get_ticks())
        start = time.time()

        while self._get_ticks() < 0:
            self._set_esc(_GRIPPER_ESC_OPEN_FAST)
            if time.time() - start > _GRIPPER_MOTION_TIMEOUT_S:
                self._set_esc(_GRIPPER_ESC_STOP)
                raise RuntimeError(
                    f"GPIOGripper: open timed out after {_GRIPPER_MOTION_TIMEOUT_S}s "
                    f"(ticks={self._get_ticks()}, target=0)"
                )
            time.sleep(_GRIPPER_POLL_S)

        self._set_esc(_GRIPPER_ESC_STOP)
        time.sleep(0.15)
        log.info("GPIOGripper: open done (ticks=%d)", self._get_ticks())

    def close(self) -> None:
        """Fast close to _GRIPPER_CLOSE_TARGET_TICKS (1.6 revolutions)."""
        log.info(
            "GPIOGripper: closing to %d ticks (current: %d)",
            _GRIPPER_CLOSE_TARGET_TICKS, self._get_ticks(),
        )
        start = time.time()

        while self._get_ticks() > _GRIPPER_CLOSE_TARGET_TICKS:
            self._set_esc(_GRIPPER_ESC_CLOSE_FAST)
            if time.time() - start > _GRIPPER_MOTION_TIMEOUT_S:
                self._set_esc(_GRIPPER_ESC_STOP)
                raise RuntimeError(
                    f"GPIOGripper: close timed out after {_GRIPPER_MOTION_TIMEOUT_S}s "
                    f"(ticks={self._get_ticks()}, target={_GRIPPER_CLOSE_TARGET_TICKS})"
                )
            time.sleep(_GRIPPER_POLL_S)

        self._set_esc(_GRIPPER_ESC_STOP)
        time.sleep(0.15)
        log.info("GPIOGripper: close done (ticks=%d)", self._get_ticks())


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
