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

    print("Commands:")
    print("  home          — run ZERO homing routine")
    print("  pos           — query current position")
    print("  <number>      — closed-loop move to mm (requires homed encoder)")
    print("  fwd<0-100>    — raw forward power e.g. fwd30  (tests motor without encoder)")
    print("  rev<0-100>    — raw reverse power e.g. rev30")
    print("  stop          — stop motor")
    print("  q             — quit\n")

    while True:
        raw = input("cmd > ").strip().lower()
        if raw == 'q':
            print("Stopping.")
            g.stop()
            break

        elif raw == 'stop':
            g.stop()
            print("  Stopped.\n")

        elif raw == 'home':
            print("  Homing...")
            try:
                g.home()
                print(f"  Home complete. Position: {g.get_position_mm():.1f} mm\n")
            except (RuntimeError, TimeoutError) as e:
                print(f"  ERROR: {e}\n")

        elif raw == 'pos':
            print(f"  Position: {g.get_position_mm():.1f} mm\n")

        elif raw.startswith('fwd'):
            try:
                pct = int(raw[3:]) if raw[3:] else 30
                g.start(pct)
                print(f"  FWD {pct}% — press stop or send another command to halt\n")
            except ValueError:
                print("  Usage: fwd<0-100>  e.g. fwd30\n")

        elif raw.startswith('rev'):
            try:
                pct = int(raw[3:]) if raw[3:] else 30
                g.reverse(pct)
                print(f"  REV {pct}% — press stop or send another command to halt\n")
            except ValueError:
                print("  Usage: rev<0-100>  e.g. rev30\n")

        else:
            try:
                target = float(raw)
            except ValueError:
                print("  Unknown command. Type fwd30, rev30, stop, home, pos, or a mm number.\n")
                continue

            if not 0.0 <= target <= 342.0:
                print("  Out of range. Must be 0–342 mm.\n")
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
