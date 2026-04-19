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


# Current speed-control params tracked on the Python side (shadows firmware state)
_state = {
    'speed_ips':  TRAVEL_SPEED_IPS,
    'speed_kp':   2.0,
    'msp':        8.0,
    'clamp':      400,
    'speed_ctrl': True,
    'kp': 0.15, 'ki': 0.0, 'kd': 0.10,
}


def _print_help():
    print("------------------------------")
    print("  RUN<in>           GOTO with live speed log        e.g. RUN6.5")
    print("  GOTO<in>          Go to position in inches        e.g. GOTO6.5")
    print("  MM<mm>            Go to position in millimetres   e.g. MM165")
    print("  SWEEP<mm>         Slow sauce-sweep move (mm)      e.g. SWEEP80")
    print("  FWD<0-100>        Forward power                   e.g. FWD25")
    print("  REV<0-100>        Reverse power                   e.g. REV25")
    print("  SPEED<in/s>       Set target speed                e.g. SPEED3.0")
    print("  SPEEDON           Enable speed control")
    print("  SPEEDOFF          Disable speed control (raw PID)")
    print("  SKP<val>          Set speed Kp                    e.g. SKP2.0")
    print("  MSP<val>          Set max speed estimate          e.g. MSP8.0")
    print("  CLAMP<val>        Set output clamp                e.g. CLAMP300")
    print("  KP<val>           Set PID Kp                      e.g. KP0.15")
    print("  KI<val>           Set PID Ki                      e.g. KI0.0")
    print("  KD<val>           Set PID Kd                      e.g. KD0.10")
    print("  LOG               Toggle live CSV data stream")
    print("  TUNE              Show current speed-ctrl params")
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

        elif cmd == 'SPEEDON':
            g.speed_on()
            _state['speed_ctrl'] = True
            print("  Speed control ON\n")

        elif cmd == 'SPEEDOFF':
            g.speed_off()
            _state['speed_ctrl'] = False
            print("  Speed control OFF (raw PID)\n")

        elif cmd == 'TUNE':
            print("  --- Speed-control params (Python-tracked) ---")
            print(f"  speed_ctrl  : {'ON' if _state['speed_ctrl'] else 'OFF (raw PID)'}")
            print(f"  target speed: {_state['speed_ips']:.2f} in/s")
            print(f"  speed Kp    : {_state['speed_kp']}")
            print(f"  max spd est : {_state['msp']}  (feedforward scale)")
            print(f"  output clamp: {_state['clamp']}")
            print(f"  PID Kp/Ki/Kd: {_state['kp']} / {_state['ki']} / {_state['kd']}")
            print(f"  hard cap    : {MAX_SPEED_HARD_CAP} in/s")
            print("  (Use DIAG to confirm firmware-side values)\n")

        elif cmd == 'LOG':
            g.toggle_log()
            print("  LOG toggled — reading live (Ctrl-C to stop)...")
            print("  ms, pos_in, target_in, error_in, speed_in_s, esc_us")
            try:
                while True:
                    line = g.read_log_line()
                    if line:
                        print(" ", line)
            except KeyboardInterrupt:
                pass
            g.toggle_log()   # turn it back off
            print("  LOG off.\n")

        elif cmd.startswith('RUN'):
            # GOTO with live LOG streaming — lets you see speed vs position in real-time.
            # Format: ms, pos_in, target_in, error_in, speed_in_s, esc_us
            try:
                inches = float(cmd[3:])
                if not 0.0 <= inches <= MAX_TRAVEL_INCHES:
                    print(f"  Out of range. Must be 0–{MAX_TRAVEL_INCHES} in.\n")
                    continue
                import time as _time
                print(f"  RUN {inches:.4f} in at {_state['speed_ips']:.2f} in/s")
                print("  ms        pos_in   tgt_in   err_in   spd_in/s  esc_us")
                g._ser.reset_input_buffer()
                g._send(f"SPEED{_state['speed_ips']:.2f}")
                _time.sleep(0.05)
                g._ser.reset_input_buffer()
                g._send("LOG")          # enable live stream
                _time.sleep(0.02)
                g._send(f"GOTO{inches:.4f}")
                # Collect ESC pulse values alongside speeds for motor characterisation.
                # Each sample: (speed_in_s, esc_us)
                speeds = []        # raw signed speed samples
                esc_samples = []   # (abs_speed, esc_offset) pairs where esc_offset = esc_us - 1500
                start = _time.monotonic()
                arrived = False
                try:
                    while _time.monotonic() - start < 35:
                        line = g.read_log_line()
                        if not line:
                            continue
                        # CSV log line: ms,pos_in,target_in,error_in,speed_in_s,esc_us
                        if ',' in line and not line.startswith('['):
                            parts = line.split(',')
                            if len(parts) >= 6:
                                try:
                                    spd = float(parts[4])
                                    esc = int(parts[5])
                                    speeds.append(spd)
                                    esc_samples.append((abs(spd), abs(esc - 1500)))
                                except ValueError:
                                    pass
                            print(f"  {line}")
                        elif '[GOTO] Arrived' in line:
                            print(f"  {line}")
                            arrived = True
                            break
                        elif '[GOTO] Aborted' in line or '[ERR]' in line:
                            print(f"  {line}")
                            break
                        elif line.startswith('['):
                            print(f"  {line}")
                except KeyboardInterrupt:
                    g.stop()
                    print("  Interrupted — motor stopped.")
                g._send("LOG")          # disable live stream

                # ── Speed analysis ──────────────────────────────────────────
                if speeds:
                    abs_spd = [abs(s) for s in speeds]
                    target  = _state['speed_ips']

                    # Cruise samples: constant ESC offset (within ±5 us), moving (>0.1 in/s)
                    # This isolates the steady-state motor response from ramp/decel.
                    CRUISE_ESC_OFFSET = 100  # firmware MIN_PULSE_OFFSET — floor for all output
                    cruise = [s for s, e in esc_samples
                              if abs(e - CRUISE_ESC_OFFSET) <= 5 and s > 0.2]
                    avg_cruise = sum(cruise) / len(cruise) if cruise else (sum(abs_spd) / len(abs_spd))

                    # Extrapolate real motor max speed from cruise observation:
                    #   At ESC offset = CRUISE_ESC_OFFSET, motor = avg_cruise in/s
                    #   Linear extrapolation to full clamp
                    est_max = avg_cruise * (_state['clamp'] / CRUISE_ESC_OFFSET) if avg_cruise > 0 else _state['msp']

                    # Min controllable speed: feedforward output at target must exceed MIN_PULSE_OFFSET.
                    #   baseOutput = (target / MSP) * CLAMP
                    #   Need baseOutput >= MIN_PULSE_OFFSET
                    #   → min target = MIN_PULSE_OFFSET / CLAMP * MSP
                    # But since we now know real max speed, the tighter constraint is:
                    #   At MIN_PULSE_OFFSET, motor goes avg_cruise in/s → that IS the floor.
                    min_ctrl = avg_cruise  # can't go slower at steady state

                    print("  ─── Speed analysis ───────────────────────────────")
                    print(f"  Target speed      : {target:.2f} in/s")
                    print(f"  Peak speed        : {max(abs_spd):.3f} in/s")
                    print(f"  Avg @ min-pulse   : {avg_cruise:.2f} in/s  "
                          f"({len(cruise)} cruise samples at ESC offset={CRUISE_ESC_OFFSET})")
                    print(f"  Est. motor max    : {est_max:.1f} in/s  "
                          f"(extrapolated at CLAMP={_state['clamp']})")
                    print(f"  Min controllable  : ~{min_ctrl:.1f} in/s  "
                          f"(limited by MIN_PULSE_OFFSET=100, hardcoded in firmware)")
                    print()

                    if target < min_ctrl * 0.85:
                        # Target is below the floor — speed control fundamentally cannot hold it.
                        fw_min = int(CRUISE_ESC_OFFSET * target / avg_cruise) if avg_cruise > 0 else 30
                        print(f"  ⚠ TARGET {target:.2f} in/s IS BELOW THE MINIMUM CONTROLLABLE SPEED")
                        print(f"    The firmware floors all output at MIN_PULSE_OFFSET=100.")
                        print(f"    At that floor the motor runs at ~{avg_cruise:.1f} in/s regardless of SPEED setting.")
                        print(f"    Speed feedback correction is capped at ±50 in firmware,")
                        print(f"    so it cannot pull output below the 100-unit floor.")
                        print()
                        print(f"  Recommended fixes (pick one):")
                        print(f"    1. SPEEDOFF  — use raw PID; naturally decelerates near target (best for positioning)")
                        print(f"    2. Firmware: lower MIN_PULSE_OFFSET from 100 to ~{max(fw_min, 10)}")
                        print(f"       (in autosauce_testing.ino: #define MIN_PULSE_OFFSET {max(fw_min, 10)})")
                        print(f"    3. Increase SPEED to ≥{min_ctrl:.1f} in/s if fast moves are acceptable")
                    else:
                        # Target is in range — MSP calibration may still be off.
                        rec_msp = round(est_max, 1)
                        print(f"  MSP calibration   : current={_state['msp']:.1f}, recommended={rec_msp}")
                        if abs(rec_msp - _state['msp']) > 1.0:
                            print(f"    → try: MSP{rec_msp}")
                        else:
                            print(f"    → MSP looks reasonable; tune SKP if overshoot persists")

                    over  = [s for s in abs_spd if s > target * 1.1]
                    under = [s for s in abs_spd if s < target * 0.9 and s > 0.1]
                    print(f"  >10% over target  : {len(over)} / {len(abs_spd)} samples")
                    print(f"  >10% under target : {len(under)} / {len(abs_spd)} samples")
                    print("  ──────────────────────────────────────────────────")

                if not arrived:
                    g._position_mm = -1
                print()
            except ValueError:
                print("  Usage: RUN<inches>  e.g. RUN6.5\n")

        elif cmd.startswith('GOTO'):
            try:
                inches = float(cmd[4:])
                print(f"  GOTO {inches:.4f} in (blocking)...")
                g.set_speed(_state['speed_ips'])
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
                _state['speed_ips'] = ips
                print(f"  Target speed set to {ips:.2f} in/s\n")
            except ValueError:
                print(f"  Usage: SPEED<in/s>  e.g. SPEED3.0  (max {MAX_SPEED_HARD_CAP})\n")

        elif cmd.startswith('SKP'):
            try:
                val = float(cmd[3:])
                g.set_speed_kp(val)
                _state['speed_kp'] = val
                print(f"  Speed Kp = {val}\n")
            except ValueError:
                print("  Usage: SKP<val>  e.g. SKP2.0\n")

        elif cmd.startswith('MSP'):
            try:
                val = float(cmd[3:])
                g.set_max_speed_estimate(val)
                _state['msp'] = val
                print(f"  Max speed estimate = {val}")
                print(f"  Tip: use RUN to check avg speed — lower MSP if under-speed, raise if over.\n")
            except ValueError:
                print("  Usage: MSP<val>  e.g. MSP8.0\n")

        elif cmd.startswith('CLAMP'):
            try:
                val = int(cmd[5:])
                g.set_clamp(val)
                _state['clamp'] = val
                print(f"  Output clamp = {val}\n")
            except ValueError:
                print("  Usage: CLAMP<val>  e.g. CLAMP300\n")

        elif cmd.startswith('KP'):
            try:
                val = float(cmd[2:])
                g.set_kp(val)
                _state['kp'] = val
                print(f"  Kp = {val}\n")
            except ValueError:
                print("  Usage: KP<val>  e.g. KP0.15\n")

        elif cmd.startswith('KI'):
            try:
                val = float(cmd[2:])
                g.set_ki(val)
                _state['ki'] = val
                print(f"  Ki = {val}\n")
            except ValueError:
                print("  Usage: KI<val>  e.g. KI0.0\n")

        elif cmd.startswith('KD'):
            try:
                val = float(cmd[2:])
                g.set_kd(val)
                _state['kd'] = val
                print(f"  Kd = {val}\n")
            except ValueError:
                print("  Usage: KD<val>  e.g. KD0.10\n")

        elif raw == '':
            pass

        else:
            print("  Unknown command. Type HELP.\n")


if __name__ == "__main__":
    main()
