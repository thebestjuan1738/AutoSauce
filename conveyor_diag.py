"""
Conveyor encoder diagnostic — run while plugged into the Conveyor Uno.
Sends STATUS repeatedly and prints encoder position so you can watch it
change (or not) while manually spinning the belt/motor shaft.
"""

import serial
import time
import threading

PORT = "COM5"
BAUD = 9600

def read_loop(ser):
    while True:
        try:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if line:
                print(f"  << {line}")
        except Exception:
            break

def send(ser, cmd):
    print(f"\n  >> {cmd}")
    ser.write((cmd + "\n").encode())
    time.sleep(0.3)

print(f"Connecting to {PORT} @ {BAUD}...")
with serial.Serial(PORT, BAUD, timeout=1) as ser:
    time.sleep(2)  # wait for Arduino reset after connect
    ser.reset_input_buffer()

    t = threading.Thread(target=read_loop, args=(ser,), daemon=True)
    t.start()

    print("\n=== CONVEYOR ENCODER DIAGNOSTIC ===")
    print("Commands:")
    print("  s         → STATUS (shows absEncoder position in mm)")
    print("  h         → HOME (zero encoder)")
    print("  f<mm>     → FWD<mm> (e.g. f50 = move fwd 50mm)")
    print("  stop      → CONVSTOP")
    print("  q         → quit")
    print()
    print("TIP: Send 's', then spin the belt by hand, then 's' again.")
    print("     If position doesn't change → encoder not counting.\n")

    while True:
        try:
            raw = input("cmd> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if raw == "q":
            break
        elif raw == "s":
            send(ser, "STATUS")
        elif raw == "h":
            send(ser, "HOME")
        elif raw == "stop":
            send(ser, "CONVSTOP")
        elif raw.startswith("f"):
            dist = raw[1:].strip()
            send(ser, f"FWD{dist}")
        elif raw:
            send(ser, raw.upper())

print("Done.")
