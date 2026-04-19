"""
test_gantry.py — Interactive gantry movement tester.

Run from the repo root on the Pi:
    cd ~/AutoSauce && python3 test_gantry.py

Steps:
    1. Connects to the gantry Arduino (CP210x on /dev/ttyUSB0)
    2. Runs the homing routine (ZERO) to establish position = 0
    3. Prompts for a target position in mm, moves there, reports result
    4. Repeat until you type 'q'
"""

from pi.motion.vesc_gantry import VESCGantry
from pi.utils.logger import log


def main():
    print("\n=== Gantry Tester ===")
    print("Connecting...")
    g = VESCGantry()
    g.boot_check()
    print(f"Connected. Current position (unzeroed): {g.get_position_mm():.1f} mm\n")

    print("Homing gantry (reversing to limit switch)...")
    g.home()
    print(f"Home complete. Position: {g.get_position_mm():.1f} mm\n")

    print("Enter a target position in mm (0–342), or 'q' to quit.")
    print("Arduino hard limits: min=0 mm  max=342 mm (13.5 in)\n")

    while True:
        raw = input("Target mm > ").strip().lower()
        if raw == 'q':
            print("Stopping.")
            g.stop()
            break

        try:
            target = float(raw)
        except ValueError:
            print("  Enter a number or 'q'.")
            continue

        if not 0.0 <= target <= 342.0:
            print("  Out of range. Must be 0–342 mm.")
            continue

        print(f"  Moving to {target:.1f} mm...")
        try:
            g.move_to(int(target))
            pos = g.get_position_mm()
            print(f"  Arrived. Reported position: {pos:.1f} mm\n")
        except (TimeoutError, RuntimeError) as e:
            print(f"  ERROR: {e}\n")


if __name__ == "__main__":
    main()
