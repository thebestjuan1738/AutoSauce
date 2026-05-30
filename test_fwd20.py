"""
test_fwd20.py — Minimal motor response test. No auto-home, no boot_check.

Connects directly to the ESP8266, waits for STATUS at neutral (ESC armed),
then sends FWD50 WITHOUT sending STOP first. The STOP call in boot_check
triggers esc.writeMicroseconds(1500) which may glitch the PWM long enough
to de-arm the ESC. This version avoids that entirely.

Run on Pi:  python3 test_fwd20.py
"""

import serial
import serial.tools.list_ports
import time

BAUD          = 115200
WAIT_S        = 25    # max wait for STATUS at neutral (covers full boot + ESC arming)
HOLD_NEUTRAL  = 5     # seconds to observe neutral before commanding — ensures ESC is armed
RUN_S         = 5     # how long to run FWD
FWD_POWER     = 10    # 10% forward
FALLBACK_PORT = "/dev/ttyGANTRY"

GANTRY_VID = 0x10C4
GANTRY_PID = 0xEA60

def find_port():
    for p in serial.tools.list_ports.comports():
        if p.vid == GANTRY_VID and p.pid == GANTRY_PID:
            print(f"[detect] CP210x on {p.device}  ({p.description})")
            return p.device
    print(f"[detect] CP210x not found — using {FALLBACK_PORT}")
    return FALLBACK_PORT

def send(ser, cmd):
    print(f"  > {cmd}")
    ser.write(f"{cmd}\n".encode())
    ser.flush()

def readline(ser):
    try:
        return ser.readline().decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""

def main():
    port = find_port()
    print(f"Opening {port} @ {BAUD} baud...")
    ser = serial.Serial(port, BAUD, timeout=0.5)
    print("Port open. NOT sending STOP — letting ESC stay armed.\n")

    # ── Wait for STATUS at neutral (ESC should be armed from setup()) ─────────
    print(f"Waiting up to {WAIT_S}s for [STATUS] with ESC: 1500us (neutral = armed)...")
    deadline = time.monotonic() + WAIT_S
    ready = False
    while time.monotonic() < deadline:
        line = readline(ser)
        if not line:
            continue
        print(f"  < {line}")
        if "[STATUS]" in line and "ESC: 1500us" in line:
            ready = True
            break
        elif "[STATUS]" in line:
            # Print but keep waiting for neutral confirmation
            pass

    if not ready:
        print("[WARN] Did not see ESC: 1500us in STATUS — ESC may not be at neutral\n")
    else:
        print(f"[OK] ESC confirmed at neutral (1500us).\n")

    # ── Hold and observe for HOLD_NEUTRAL seconds before commanding ───────────
    print(f"Observing {HOLD_NEUTRAL}s more at neutral (ensures ESC fully armed)...")
    start = time.monotonic()
    while time.monotonic() - start < HOLD_NEUTRAL:
        line = readline(ser)
        if line:
            print(f"  < {line}")

    # ── NO STOP sent — go straight to FWD ────────────────────────────────────
    print(f"\nSending FWD{FWD_POWER} — reading STATUS for {RUN_S}s:")
    print("  (watch Pos or ActSpd for any change)\n")
    ser.reset_input_buffer()
    send(ser, f"FWD{FWD_POWER}")

    positions = []
    speeds    = []
    start = time.monotonic()
    while time.monotonic() - start < RUN_S:
        line = readline(ser)
        if not line:
            continue
        print(f"  < {line}")
        if "[STATUS]" in line:
            try:
                pos = float(line.split("Pos:")[1].split("in")[0].strip())
                spd = float(line.split("ActSpd:")[1].split("in/s")[0].strip())
                positions.append(pos)
                speeds.append(abs(spd))
            except (IndexError, ValueError):
                pass

    # ── Stop ─────────────────────────────────────────────────────────────────
    send(ser, "STOP")
    time.sleep(0.2)
    ser.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print("RESULT SUMMARY")
    print("=" * 50)
    if positions:
        pos_range = max(positions) - min(positions)
        max_spd   = max(speeds) if speeds else 0.0
        print(f"  Position range : {min(positions):.4f} → {max(positions):.4f} in  (Δ {pos_range:.4f} in)")
        print(f"  Peak speed     : {max_spd:.4f} in/s")
        if pos_range > 0.01 or max_spd > 0.05:
            print("  MOTOR MOVING  ✓")
        else:
            print("  MOTOR NOT MOVING ✗ — position/speed stuck")
    else:
        print("  No STATUS lines parsed — check port / firmware")
    print("=" * 50)

if __name__ == "__main__":
    main()
