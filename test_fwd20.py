"""
test_fwd20.py — Minimal motor response test. No auto-home, no boot_check.

Connects directly to the ESP8266, waits for STATUS, sends FWD20, reads
STATUS for 5 seconds watching for position/speed changes, then stops.

Run on Pi:  python3 test_fwd20.py
"""

import serial
import serial.tools.list_ports
import time

BAUD         = 115200
WAIT_S       = 20    # max wait for first STATUS line (covers full ESC arming)
RUN_S        = 5     # how long to run FWD20
FWD_POWER    = 20
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
    print(f"Opening {port} @ {BAUD} baud (no DTR manipulation)...")
    ser = serial.Serial(port, BAUD, timeout=0.5)
    print("Port open.\n")

    # ── Wait for firmware to be alive ─────────────────────────────────────────
    print(f"Waiting up to {WAIT_S}s for [STATUS] line (covers DTR-reset + ESC arming)...")
    deadline = time.monotonic() + WAIT_S
    ready = False
    while time.monotonic() < deadline:
        line = readline(ser)
        if not line:
            continue
        print(f"  < {line}")
        if "[STATUS]" in line or "[POS]" in line:
            ready = True
            break

    if not ready:
        print("[WARN] No STATUS seen — continuing anyway (firmware may be mid-boot)\n")
    else:
        print("[OK] Firmware alive.\n")

    # ── Send STOP to clear any leftover motion ────────────────────────────────
    ser.reset_input_buffer()
    send(ser, "STOP")
    time.sleep(0.2)

    # ── Baseline POS ──────────────────────────────────────────────────────────
    ser.reset_input_buffer()
    send(ser, "POS")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        line = readline(ser)
        if line:
            print(f"  < {line}")
            if "[POS]" in line:
                break

    # ── FWD run ───────────────────────────────────────────────────────────────
    print(f"\nSending FWD{FWD_POWER} — reading STATUS for {RUN_S}s:")
    print("  (watch for Pos or ActSpd to change — any change = motor moving)\n")
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

    # ── Stop ──────────────────────────────────────────────────────────────────
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
            print("  MOTOR MOVING  ✓ — encoder sees motion from Pi")
        else:
            print("  MOTOR NOT MOVING ✗ — position/speed stuck despite ESC commanded")
    else:
        print("  No STATUS lines parsed — check port / firmware")
    print("=" * 50)

if __name__ == "__main__":
    main()
