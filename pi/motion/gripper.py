"""
gripper.py — Gripper driver for the SauceBot.

Hardware:
    Motor controller signal  ◄── Pi GPIO 12  (hardware PWM via gpiozero/lgpio)
    Encoder A                ──► Pi GPIO 16
    Encoder B                ──► Pi GPIO 20
"""

import time
import threading

import RPi.GPIO as GPIO
from gpiozero import Servo

from pi.utils.logger import log
from pi.motion._shared import PWM_FREQ, _ESC_MIN_US, _ESC_MAX_US, _esc_value

# ─── GPIO pin assignments (BCM numbering) ─────────────────────────────────────
PIN_ESC       = 12    # PWM signal to goBILDA 1x20A controller
PIN_ENCODER_A = 16    # encoder channel A
PIN_ENCODER_B = 20    # encoder channel B

# ─── ESC pulse widths (microseconds) ──────────────────────────────────────────
_ESC_STOP        = 1500
_ESC_OPEN_STRONG = 1650   # strong torque, homing phase 1
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
        self.close_limit_ticks = None

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

        log.info(
            "GPIOGripper: ESC on GPIO %d, encoder on GPIO %d/%d",
            PIN_ESC, PIN_ENCODER_A, PIN_ENCODER_B,
        )
        # We need to give the ESC its expected arming signals, but NOT actually 
        # drive the motor MAX and MIN. The goBILDA ESCs only need to see 1500us
        # to arm and initialize! If we send 1900 and 1100 before that it actually
        # jerks the motor wildly or puts it into calibration mode.
        log.info("GPIOGripper: arming ESC (holding stop/neutral)...")
        self._set_esc(_ESC_STOP)
        time.sleep(2.0)
        log.info("GPIOGripper: ESC armed")

        self.home()

    def cleanup(self) -> None:
        """Disarm ESC and release GPIO resources. Call on shutdown."""
        self._set_esc(_ESC_STOP)
        self._esc.value = 0
        self._esc.detach()
        self._esc.close()
        GPIO.cleanup([PIN_ENCODER_A, PIN_ENCODER_B])
        log.info("GPIOGripper: cleanup done")

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
        Pre-move: nudge closed briefly to clear the mechanical open stop.
        Phase 1: strong open torque until movement is detected.
        Phase 2: slow creep until stall at mechanical open limit.
        Zeroes the encoder once stall is confirmed.
        Phase 3: slow creep to determine mechanical closed limit.
        Phase 4: Return to open position.
        """
        # Detect current position to handle different starting states
        current_ticks = self._get_ticks()
        log.info("GPIOGripper: homing — starting at ticks=%d", current_ticks)
        
        # We don't have accurate ticks yet since we just powered on, but if there's any residual
        # we can nudge it. However, the best bet is to just pull it closed a TINY bit and then open.
        log.info("GPIOGripper: homing — pre-close nudge (clearing open stop)...")
        self._set_esc(_ESC_CLOSE_FAST)
        time.sleep(0.3)
        self._set_esc(_ESC_STOP)
        time.sleep(0.1)

        log.info("GPIOGripper: homing — phase 1 (strong open torque)...")
        last_ticks = self._get_ticks()

        start = time.time()
        self._set_esc(_ESC_OPEN_STRONG)
        
        # Give it a tiny moment to build speed before checking first tick
        time.sleep(0.2)
        
        while True:
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
        self._set_esc(_ESC_OPEN_SLOW)
        
        # Allow inertia to transfer before checking for stalls
        time.sleep(0.5)
        
        last_ticks     = self._get_ticks()
        last_move_time = time.time()

        while True:
            current = self._get_ticks()
            if current != last_ticks:
                last_ticks     = current
                last_move_time = time.time()

            if (time.time() - last_move_time) * 1000 > 400: # Increased from 200ms to 400ms for safety
                self._set_esc(_ESC_STOP)
                time.sleep(0.2)
                self._zero_ticks()
                log.info("GPIOGripper: open limit established — encoder zeroed")
                break

            time.sleep(_POLL_S)

        log.info("GPIOGripper: homing — phase 3 (closing to find close limit)...")
        
        # Record the start time BEFORE moving so we don't inadvertently stall during a long sleep
        last_ticks = self._get_ticks()
        last_move_time = time.time()
        
        self._set_esc(_ESC_CLOSE_FAST)
        
        # Wait a very brief amount of time for it to begin moving (instead of 1.0s which was
        # hiding the movement from the stall detector because it had already finished moving!)
        time.sleep(0.2) 
        
        start = time.time()
        while True:
            current = self._get_ticks()
            if current != last_ticks:
                last_ticks = current
                last_move_time = time.time()

            if (time.time() - last_move_time) * 1000 > 400:
                self._set_esc(_ESC_STOP)
                self.close_limit_ticks = self._get_ticks()
                time.sleep(0.2)
                log.info("GPIOGripper: close limit established at %d ticks", self.close_limit_ticks)
                break
            
            if time.time() - start > 15.0:
                self._set_esc(_ESC_STOP)
                raise RuntimeError(f"GPIOGripper: timed out finding close limit, ended at {self._get_ticks()} ticks")
            
            time.sleep(_POLL_S)
            
        log.info("GPIOGripper: homing — phase 4 (returning to open limit)...")
        self.open()

    def open(self) -> None:
        """Fast return to encoder zero (open position)."""
        log.info("GPIOGripper: opening (current ticks: %d)", self._get_ticks())
        start = time.time()

        self._set_esc(_ESC_OPEN_FAST)
        # Bypassing the immediate target=0 check incase inertia takes a second to register
        time.sleep(1.0)
        
        while self._get_ticks() > -100:  # Adding a tiny buffer so it doesn't instantly think it's done at -70
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
        """Fast close until it stalls against an object (grabbing)."""
        log.info("GPIOGripper: closing until stall (grabbing object)...")
        start = time.time()
        
        self._set_esc(_ESC_CLOSE_FAST)
        # Give motor a solid moment to overcome inertia before tracking stalls
        time.sleep(1.0)
        
        last_ticks = self._get_ticks()
        last_move_time = time.time()

        while True:
            current = self._get_ticks()
            if current != last_ticks:
                last_ticks = current
                last_move_time = time.time()

            # Use a higher stall detection threshold for closing (400ms) to avoid false 
            # positives if the software PWM or OS scheduling stutters slightly
            if (time.time() - last_move_time) * 1000 > 400:
                self._set_esc(_ESC_STOP)
                time.sleep(0.15)
                log.info("GPIOGripper: close grabbed/stalled at %d ticks", self._get_ticks())
                return

            # Keep a generous absolute maximum timeout just in case the encoder completely breaks
            if time.time() - start > 15.0:
                self._set_esc(_ESC_STOP)
                raise RuntimeError(
                    f"GPIOGripper: close timed out after 15.0s "
                    f"(ticks={self._get_ticks()})"
                )
            time.sleep(_POLL_S)
