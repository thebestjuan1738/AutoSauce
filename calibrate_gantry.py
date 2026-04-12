"""
calibrate_gantry.py

Interactive calibration script for the VESCGantry.
Loops until you confirm the results look consistent across multiple runs.

Usage:
    python calibrate_gantry.py

Each iteration:
    1. Drives the gantry forward for 5 seconds at TRAVEL_SPEED.
    2. Returns it to the start position using the current best estimate.
    3. You measure the distance travelled and enter it.
    4. Results are shown. Repeat until readings are consistent, then confirm.
"""

import sys
import time

# Allow running from the repo root without installing the package.
sys.path.insert(0, ".")

from pi.motion.vesc_gantry import VESCGantry, TRAVEL_SPEED, MAX_DUTY_GANTRY

CALIBRATION_DURATION_S = 5.0   # how long to run the motor during each test run


def _run_timed(gantry: VESCGantry, duration_s: float) -> int:
    """Drive forward for duration_s, return tick delta."""
    t_before = gantry._get_encoder_position()
    gantry.start(TRAVEL_SPEED)
    for remaining in range(int(duration_s), 0, -1):
        print(f"  {remaining}s remaining...", end="\r", flush=True)
        time.sleep(1.0)
    gantry.stop()
    print("  Motor stopped.              ", flush=True)
    time.sleep(0.3)   # let carriage coast to rest
    t_after = gantry._get_encoder_position()
    return abs(t_after - t_before), t_before, t_after


def _return_to_start(gantry: VESCGantry, tick_delta: int) -> None:
    """Drive in reverse for the same tick count to return to the start position."""
    print("  Returning to start...", flush=True)
    t_before = gantry._get_encoder_position()
    target   = t_before - tick_delta        # reverse direction
    gantry.reverse(TRAVEL_SPEED)
    deadline = time.monotonic() + 15.0
    while True:
        current = gantry._get_encoder_position()
        if abs(current - target) <= 5:
            break
        if time.monotonic() > deadline:
            break
        time.sleep(0.05)
    gantry.stop()
    time.sleep(0.3)
    print("  Back at start.        ", flush=True)


def _ask_float(prompt: str) -> float:
    while True:
        raw = input(prompt).strip()
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
        print("  Please enter a positive number.")


def _ask_yn(prompt: str) -> bool:
    while True:
        raw = input(prompt).strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please enter y or n.")


def main():
    print("=" * 60)
    print("  VESCGantry Calibration Tool")
    print("=" * 60)
    print()
    print(f"Each run drives the gantry forward for {CALIBRATION_DURATION_S:.0f}s,")
    print("then returns it to the start. Repeat until results are consistent.")
    print()
    print("BEFORE CONTINUING:")
    print("  - Carriage must be near the DOCK end of the rail.")
    print("  - At least 300 mm of clear travel required.")
    print("  - Keep hands clear of the mechanism.")
    print()
    input("Press Enter when ready, or Ctrl-C to abort...")
    print()

    # ── Connect ───────────────────────────────────────────────────────────────
    print("Connecting to VESC...")
    try:
        gantry = VESCGantry()
    except Exception as exc:
        print(f"\nERROR: Could not connect to VESC: {exc}")
        sys.exit(1)

    print("Running boot check...")
    try:
        gantry.boot_check()
    except RuntimeError as exc:
        print(f"\nERROR: Boot check failed:\n{exc}")
        sys.exit(1)
    print()

    # ── Calibration loop ──────────────────────────────────────────────────────
    run_number   = 0
    all_results  = []          # list of (tick_delta, actual_mm, ticks_per_mm)
    best_tpm     = None        # best TICKS_PER_MM so far (used for return move)

    while True:
        run_number += 1
        print(f"─── Run {run_number} " + "─" * (52 - len(str(run_number))))
        print()

        # Drive forward
        print(f"Driving forward at speed={TRAVEL_SPEED} "
              f"(duty={TRAVEL_SPEED / 100.0 * MAX_DUTY_GANTRY:.2f}) "
              f"for {CALIBRATION_DURATION_S:.0f}s...")
        tick_delta, t_before, t_after = _run_timed(gantry, CALIBRATION_DURATION_S)
        print(f"  Ticks before : {t_before}")
        print(f"  Ticks after  : {t_after}")
        print(f"  Tick delta   : {tick_delta}")
        print()

        if tick_delta == 0:
            print("WARNING: No ticks registered. Motor may not have moved.")
            print("Check wiring and VESC Tool motor detection, then try again.")
            print()
            if not _ask_yn("Try again? [y/n]: "):
                print("Aborted.")
                sys.exit(1)
            continue

        # Ask for measured distance
        print("Measure the distance the carriage physically moved (ruler).")
        print("Include coasting distance after the motor stopped.")
        actual_mm   = _ask_float("Measured distance in mm: ")
        ticks_per_mm = tick_delta / actual_mm
        tolerance_ticks  = max(1, round(ticks_per_mm * 2))
        position_error_mm = tolerance_ticks / ticks_per_mm
        best_tpm = ticks_per_mm   # use latest estimate for return move

        all_results.append((tick_delta, actual_mm, ticks_per_mm))

        # Show this run's result
        print()
        print(f"  Run {run_number} result:")
        print(f"    {tick_delta} ticks  /  {actual_mm} mm  →  TICKS_PER_MM = {ticks_per_mm:.2f}")
        print()

        # Show history if more than one run
        if len(all_results) > 1:
            tpm_values = [r[2] for r in all_results]
            avg = sum(tpm_values) / len(tpm_values)
            spread = max(tpm_values) - min(tpm_values)
            print(f"  History ({len(all_results)} runs):")
            for i, (td, mm, tpm) in enumerate(all_results, 1):
                print(f"    Run {i}: {td} ticks / {mm:.1f} mm = {tpm:.2f} ticks/mm")
            print(f"  Average : {avg:.2f}  |  Spread: {spread:.2f}")
            print()

        # Return to start
        print("Returning carriage to start position...")
        _return_to_start(gantry, tick_delta)
        print()
        print("Reposition the carriage back to the marked start point if needed.")
        print()

        # Accept or repeat
        if _ask_yn("Accept these results and finish? [y/n]: "):
            break

        print()
        input("Reposition carriage to start, then press Enter to run again...")
        print()

    # ── Final output ──────────────────────────────────────────────────────────
    # Use average if multiple runs, otherwise single result.
    if len(all_results) > 1:
        final_tpm = sum(r[2] for r in all_results) / len(all_results)
        print(f"\nUsing average of {len(all_results)} runs.")
    else:
        final_tpm = all_results[0][2]

    final_tolerance = max(1, round(final_tpm * 2))
    final_error_mm  = final_tolerance / final_tpm

    print()
    print("=" * 60)
    print("  FINAL CALIBRATION VALUES")
    print("=" * 60)
    print()
    print(f"  TICKS_PER_MM             = {final_tpm:.1f}")
    print(f"  POSITION_TOLERANCE_TICKS = {final_tolerance}   (±{final_error_mm:.1f} mm)")
    print()
    print("─" * 60)
    print("  Paste into pi/motion/vesc_gantry.py:")
    print("─" * 60)
    print()
    print(f"TICKS_PER_MM             = {final_tpm:.1f}")
    print(f"POSITION_TOLERANCE_TICKS = {final_tolerance}")
    print()
    print("─" * 60)
    print()


if __name__ == "__main__":
    main()
