"""
gantry.py — Gantry driver for the SauceBot.

Hardware:
    Turnigy SK8 V2 VESC over UART (PyVESC), closed-loop encoder position.

Requirements:
    - VESC Tool motor detection + encoder setup done once before first use
    - UART enabled on the Pi (raspi-config → Interface Options → Serial)
    - TICKS_PER_MM calibrated to actual belt/pulley ratio
"""

import time

from pyvesc import VESC

from pi.utils.logger import log

# ─── VESC / UART ──────────────────────────────────────────────────────────────
SERIAL_PORT = "/dev/ttyAMA0"
BAUD_RATE   = 115200

# TODO: tune TRAVEL_RPM once motor detection is done in VESC Tool
TRAVEL_RPM  = 3000

# TODO: calibrate from (encoder PPR × gear/pulley ratio) after VESC Tool setup
# e.g.  TICKS_PER_MM = encoder_PPR * motor_turns_per_mm
TICKS_PER_MM = 100

# How close (in ticks) counts as "arrived"
POSITION_TOLERANCE_TICKS = 50

# Raise TimeoutError if the gantry doesn't reach the target within this many seconds
TRAVEL_TIMEOUT_S = 30


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

        start_ticks  = self._get_encoder_position()
        target_ticks = start_ticks + direction * delta_ticks

        log.info(
            "GPIOGantry: %dmm → %dmm  (Δ%d ticks, RPM=%d)",
            self._position_mm, position_mm, direction * delta_ticks, TRAVEL_RPM,
        )

        self._vesc.set_rpm(direction * TRAVEL_RPM)

        deadline = time.monotonic() + TRAVEL_TIMEOUT_S
        while True:
            current_ticks   = self._get_encoder_position()
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
