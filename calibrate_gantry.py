"""
calibrate_gantry.py

Interactive calibration script for the VESCGantry.
Run this once on the Pi after the motor is confirmed to be moving.

Usage:
    python calibrate_gantry.py

Steps:
    1. Script connects to the VESC and runs a boot check.
    2. You confirm the gantry has clear travel (300+ mm).
    3. The gantry runs forward for 5 seconds at TRAVEL_SPEED.
    4. You measure the physical distance the carriage moved with a ruler.
    5. You enter the measured distance (mm) at the prompt.
    6. Script prints the exact values to paste into vesc_gantry.py.
"""

import sys
import time

# Allow running from the repo root without installing the package.
sys.path.insert(0, ".")

from pi.motion.vesc_gantry import VESCGantry, TRAVEL_SPEED, MAX_DUTY_GANTRY
from pi.utils.logger import log

CALIBRATION_DURATION_S = 5.0   # how long to run the motor during the test


def main():
    print("=" * 60)
    print("  VESCGantry Calibration Tool")
    print("=" * 60)
    print()
    print("This script will drive the gantry forward for "
          f"{CALIBRATION_DURATION_S:.0f} seconds.")
    print("You will then measure the physical distance with a ruler")
    print("and enter it here.")
    print()
    print("BEFORE CONTINUING:")
    print("  - Make sure the carriage is near the DOCK end of the rail.")
    print("  - Make sure there is at least 300 mm of clear travel.")
    print("  - Keep hands clear of the mechanism.")
    print()

    input("Press Enter when ready, or Ctrl-C to abort...")
    print()

    # ── Connect ──────────────────────────────────────────────────────────────
    print("Connecting to VESC...")
    try:
        gantry = VESCGantry()
    except Exception as exc:
        print(f"\nERROR: Could not connect to VESC: {exc}")
        print("Check that the USB cable is plugged in and the port is correct.")
        sys.exit(1)

    # ── Boot check ───────────────────────────────────────────────────────────
    print("Running boot check...")
    try:
        gantry.boot_check()
    except RuntimeError as exc:
        print(f"\nERROR: Boot check failed:\n{exc}")
        sys.exit(1)
    print()

    # ── Read starting tick count ─────────────────────────────────────────────
    try:
        ticks_before = gantry._get_encoder_position()
    except Exception as exc:
        print(f"ERROR: Could not read encoder: {exc}")
        sys.exit(1)

    print(f"Encoder position before: {ticks_before} ticks")
    print()
    print(f"Driving forward at speed={TRAVEL_SPEED} "
          f"(duty={TRAVEL_SPEED / 100.0 * MAX_DUTY_GANTRY:.2f}) "
          f"for {CALIBRATION_DURATION_S:.0f} seconds...")
    print()

    # ── Run ─────────────────────────────────────────────────────────────────
    gantry.start(TRAVEL_SPEED)
    for remaining in range(int(CALIBRATION_DURATION_S), 0, -1):
        print(f"  {remaining}s remaining...", end="\r")
        time.sleep(1.0)
    gantry.stop()
    print("  Motor stopped.              ")
    print()

    # ── Read ending tick count ───────────────────────────────────────────────
    time.sleep(0.3)   # let the carriage coast to a stop
    try:
        ticks_after = gantry._get_encoder_position()
    except Exception as exc:
        print(f"ERROR: Could not read encoder after run: {exc}")
        sys.exit(1)

    tick_delta = abs(ticks_after - ticks_before)
    print(f"Encoder position after : {ticks_after} ticks")
    print(f"Tick delta             : {tick_delta} ticks")
    print()

    if tick_delta == 0:
        print("WARNING: No encoder ticks registered — the motor may not have moved.")
        print("Check wiring, duty cycle, and that motor detection was run in VESC Tool.")
        sys.exit(1)

    # ── Get measured distance ────────────────────────────────────────────────
    print("Measure the distance the carriage physically travelled with a ruler.")
    print("Include any coasting distance after the motor stopped.")
    print()

    while True:
        raw = input("Enter measured distance in mm (e.g. 247.5): ").strip()
        try:
            actual_mm = float(raw)
            if actual_mm <= 0:
                raise ValueError
            break
        except ValueError:
            print("  Please enter a positive number.")

    # ── Calculate values ─────────────────────────────────────────────────────
    ticks_per_mm        = tick_delta / actual_mm
    tolerance_ticks     = max(1, round(ticks_per_mm * 2))   # ±2 mm
    position_error_mm   = tolerance_ticks / ticks_per_mm

    print()
    print("=" * 60)
    print("  CALIBRATION RESULTS")
    print("=" * 60)
    print()
    print(f"  Tick delta   : {tick_delta} ticks")
    print(f"  Actual travel: {actual_mm} mm")
    print(f"  TICKS_PER_MM : {ticks_per_mm:.4f}  →  {ticks_per_mm:.1f}")
    print(f"  Tolerance    : ±{position_error_mm:.1f} mm  ({tolerance_ticks} ticks)")
    print()
    print("─" * 60)
    print("  Paste these values into pi/motion/vesc_gantry.py:")
    print("─" * 60)
    print()
    print(f"  TICKS_PER_MM             = {ticks_per_mm:.1f}")
    print(f"  POSITION_TOLERANCE_TICKS = {tolerance_ticks}")
    print()
    print("─" * 60)
    print()


if __name__ == "__main__":
    main()
