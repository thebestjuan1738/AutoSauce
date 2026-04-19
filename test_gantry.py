"""
test_gantry.py — Interactive gantry tester for the HiLetGo NodeMCU ESP8266 controller.

USB plugs directly into the HiLetGo — no Arduino board involved.
Run from the repo root:
    python test_gantry.py          (Windows)
    python3 test_gantry.py         (Pi / Linux)

Exposes every firmware command. Type HELP for the full command list.
"""

from pi.motion.vesc_gantry import (
    VESCGantry,
    MAX_TRAVEL_INCHES,
    MAX_SPEED_HARD_CAP,
    SWEEP_MAX_DUTY,
    SWEEP_SPEED_IPS,
    TRAVEL_SPEED_IPS,
)

MAX_TRAVEL_MM = MAX_TRAVEL_INCHES * 25.4   # ~342.9 mm


def _print_help():
    print("------------------------------")
    print("  GOTO<in>          Go to position in inches       e.g. GOTO6.5")
    print("  MM<mm>            Go to position in millimetres  e.g. MM165")
    print("  SWEEP<mm>         Slow sauce-sweep move (mm)     e.g. SWEEP80")
    print("  FWD<0-100>        Forward power                  e.g. FWD25")
    print("  REV<0-100>        Reverse power                  e.g. REV25")
    print("  SPEED<in/s>       Set target speed               e.g. SPEED3.0")
    print("  SPEEDON           Enable speed control")
    print("  SPEEDOFF          Disable speed control (raw PID)")
    print("  SKP<val>          Set speed Kp                   e.g. SKP2.0")
    print("  MSP<val>          Set max speed estimate         e.g. MSP8.0")
    print("  CLAMP<val>        Set output clamp               e.g. CLAMP300")
    print("  KP<val>           Set PID Kp                     e.g. KP0.15")
    print("  KI<val>           Set PID Ki                     e.g. KI0.0")
    print("  KD<val>           Set PID Kd                     e.g. KD0.10")
    print("  LOG               Toggle live CSV data stream")
    print("  STOP / S          Stop motor immediately")
    print("  ZERO              Run homing routine")
    print("  POS  / P          Query position")
    print("  DIAG / D          Full diagnostics")
    print("  HELP / H          Show this list")
    print("  Q                 Quit")
    print("------------------------------")
    print(f"  Max travel      : {MAX_TRAVEL_INCHES} in  ({MAX_TRAVEL_MM:.1f} mm)")
    print(f"  Hard speed cap  : {MAX_SPEED_HARD_CAP} in/s")
    print(f"  Travel speed    : {TRAVEL_SPEED_IPS} in/s")
    print(f"  Sweep speed     : {SWEEP_SPEED_IPS} in/s")
    print("------------------------------\n")


def main():
    print("\n==============================")
    print("  Gantry Tester")
    print("  HiLetGo NodeMCU ESP8266")
    print("==============================")
    print("Connecting to gantry controller...")
    g = VESCGantry()
    g.boot_check()
    pos = g.get_position_mm()
    print(f"Connected. Position (unzeroed): {pos:.1f} mm\n")
    _print_help()

    while True:
        raw = input("cmd > ").strip()
        cmd = raw.upper()

        if cmd in ('Q', 'QUIT'):
            print("Stopping motor and exiting.")
            g.stop()
            break

        elif cmd in ('STOP', 'S'):
            g.stop()
            print("  Stopped.\n")

        elif cmd in ('ZERO', 'HOME'):
            print("  Homing... (may take up to 60 s)")
            try:
                g.home()
                print(f"  Homing complete. Position: {g.get_position_mm():.1f} mm\n")
            except (RuntimeError, TimeoutError) as e:
                print(f"  ERROR: {e}\n")

        elif cmd in ('POS', 'P'):
            pos = g.get_position_mm()
            pos_in = pos / 25.4
            print(f"  Position: {pos:.3f} mm  ({pos_in:.4f} in)\n")

        elif cmd in ('DIAG', 'D'):
            print(g.diag() + "\n")

        elif cmd in ('HELP', 'H'):
            _print_help()

        elif cmd in ('SPEEDON',):
            g.speed_on()
            print("  Speed control ON\n")

        elif cmd in ('SPEEDOFF',):
            g.speed_off()
            print("  Speed control OFF (raw PID)\n")

        elif cmd == 'LOG':
            g.toggle_log()
            print("  LOG toggled — reading 30 lines (Ctrl-C to stop early)...")
            try:
                for _ in range(30):
                    line = g.read_log_line()
                    if line:
                        print(" ", line)
            except KeyboardInterrupt:
                pass
            g.toggle_log()   # turn it back off
            print("  LOG off.\n")

        elif cmd.startswith('GOTO'):
            try:
                inches = float(cmd[4:])
                print(f"  GOTO {inches:.4f} in (blocking)...")
                g.set_speed(TRAVEL_SPEED_IPS)
                # Use move_to for blocking wait on [GOTO] Arrived
                mm = int(round(inches * 25.4))
                g._position_mm = -1   # force move_to to not skip
                g.move_to(mm)
                pos = g.get_position_mm()
                print(f"  Arrived. Position: {pos:.3f} mm  ({pos/25.4:.4f} in)\n")
            except ValueError:
                print("  Usage: GOTO<inches>  e.g. GOTO6.5\n")
            except (RuntimeError, TimeoutError, ValueError) as e:
                print(f"  ERROR: {e}\n")

        elif cmd.startswith('MM'):
            try:
                mm = float(cmd[2:])
                if not 0.0 <= mm <= MAX_TRAVEL_MM:
                    print(f"  Out of range. Must be 0–{MAX_TRAVEL_MM:.1f} mm.\n")
                    continue
                print(f"  Moving to {mm:.1f} mm...")
                g._position_mm = -1   # force move_to to not skip
                g.move_to(int(round(mm)))
                pos = g.get_position_mm()
                print(f"  Arrived. Position: {pos:.3f} mm  ({pos/25.4:.4f} in)\n")
            except ValueError:
                print("  Usage: MM<millimetres>  e.g. MM165\n")
            except (RuntimeError, TimeoutError) as e:
                print(f"  ERROR: {e}\n")

        elif cmd.startswith('SWEEP'):
            try:
                mm = float(cmd[5:])
                if not 0.0 <= mm <= MAX_TRAVEL_MM:
                    print(f"  Out of range. Must be 0–{MAX_TRAVEL_MM:.1f} mm.\n")
                    continue
                print(f"  Sweep move to {mm:.1f} mm at {SWEEP_SPEED_IPS} in/s...")
                g._position_mm = -1
                g.move_to(int(round(mm)), max_duty=SWEEP_MAX_DUTY)
                pos = g.get_position_mm()
                print(f"  Arrived. Position: {pos:.3f} mm\n")
            except ValueError:
                print("  Usage: SWEEP<mm>  e.g. SWEEP80\n")
            except (RuntimeError, TimeoutError) as e:
                print(f"  ERROR: {e}\n")

        elif cmd.startswith('FWD'):
            try:
                pct = int(cmd[3:]) if cmd[3:] else 25
                g.fwd(pct)
                print(f"  FWD {pct}% sent. Type STOP to halt.\n")
            except ValueError:
                print("  Usage: FWD<0-100>  e.g. FWD25\n")

        elif cmd.startswith('REV'):
            try:
                pct = int(cmd[3:]) if cmd[3:] else 25
                g.rev(pct)
                print(f"  REV {pct}% sent. Type STOP to halt.\n")
            except ValueError:
                print("  Usage: REV<0-100>  e.g. REV25\n")

        elif cmd.startswith('SPEED'):
            try:
                ips = float(cmd[5:])
                g.set_speed(ips)
                print(f"  Target speed set to {ips:.2f} in/s\n")
            except ValueError:
                print(f"  Usage: SPEED<in/s>  e.g. SPEED3.0  (max {MAX_SPEED_HARD_CAP})\n")

        elif cmd.startswith('SKP'):
            try:
                g.set_speed_kp(float(cmd[3:]))
                print(f"  Speed Kp = {cmd[3:]}\n")
            except ValueError:
                print("  Usage: SKP<val>  e.g. SKP2.0\n")

        elif cmd.startswith('MSP'):
            try:
                g.set_max_speed_estimate(float(cmd[3:]))
                print(f"  Max speed estimate = {cmd[3:]}\n")
            except ValueError:
                print("  Usage: MSP<val>  e.g. MSP8.0\n")

        elif cmd.startswith('CLAMP'):
            try:
                g.set_clamp(int(cmd[5:]))
                print(f"  Output clamp = {cmd[5:]}\n")
            except ValueError:
                print("  Usage: CLAMP<val>  e.g. CLAMP300\n")

        elif cmd.startswith('KP'):
            try:
                g.set_kp(float(cmd[2:]))
                print(f"  Kp = {cmd[2:]}\n")
            except ValueError:
                print("  Usage: KP<val>  e.g. KP0.15\n")

        elif cmd.startswith('KI'):
            try:
                g.set_ki(float(cmd[2:]))
                print(f"  Ki = {cmd[2:]}\n")
            except ValueError:
                print("  Usage: KI<val>  e.g. KI0.0\n")

        elif cmd.startswith('KD'):
            try:
                g.set_kd(float(cmd[2:]))
                print(f"  Kd = {cmd[2:]}\n")
            except ValueError:
                print("  Usage: KD<val>  e.g. KD0.10\n")

        elif raw == '':
            pass

        else:
            print("  Unknown command. Type HELP.\n")


if __name__ == "__main__":
    main()
