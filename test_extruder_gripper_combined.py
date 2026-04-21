import time
import threading
import RPi.GPIO as GPIO
from gpiozero import Device, Servo
from gpiozero.pins.lgpio import LGPIOFactory

# ─── Use lgpio (required for Pi 5) ──────────────────────
Device.pin_factory = LGPIOFactory()

# ─── CONFIG: EXTRUDER ───────────────────────────────────
PIN_EXT_ESC = 18
PIN_EXT_ENC_A = 23
PIN_EXT_ENC_B = 25

# ─── CONFIG: GRIPPER ───────────────────────────────────
PIN_GRIP_ESC = 12
PIN_GRIP_ENC_A = 16
PIN_GRIP_ENC_B = 20

PWM_FREQ = 50

# ESC pulse widths (µs)
STOP = 1500
FWD = 1650
REV = 1350
OPEN_FAST = 1650
CLOSE_FAST = 1350

# ─── GLOBAL STATE ───────────────────────────────────────
ext_ticks = 0
grip_ticks = 0
lock = threading.Lock()

# ─── HELPER ─────────────────────────────────────────────
def esc_value(pulse_us):
    return (pulse_us - 1500) / 400.0

# ─── EXTRUDER ENCODER CALLBACKS ──────────────────────────
def ext_isr_a(channel):
    global ext_ticks
    a = GPIO.input(PIN_EXT_ENC_A)
    b = GPIO.input(PIN_EXT_ENC_B)
    with lock:
        if a == b:
            ext_ticks += 1
        else:
            ext_ticks -= 1

def ext_isr_b(channel):
    global ext_ticks
    a = GPIO.input(PIN_EXT_ENC_A)
    b = GPIO.input(PIN_EXT_ENC_B)
    with lock:
        if a != b:
            ext_ticks += 1
        else:
            ext_ticks -= 1

def get_ext_ticks():
    with lock:
        return ext_ticks

# ─── GRIPPER ENCODER CALLBACKS ───────────────────────────
def grip_isr_a(channel):
    global grip_ticks
    a = GPIO.input(PIN_GRIP_ENC_A)
    b = GPIO.input(PIN_GRIP_ENC_B)
    with lock:
        if a == b:
            grip_ticks += 1
        else:
            grip_ticks -= 1

def grip_isr_b(channel):
    global grip_ticks
    a = GPIO.input(PIN_GRIP_ENC_A)
    b = GPIO.input(PIN_GRIP_ENC_B)
    with lock:
        if a != b:
            grip_ticks += 1
        else:
            grip_ticks -= 1

def get_grip_ticks():
    with lock:
        return grip_ticks

# ─── SETUP ──────────────────────────────────────────────
print("=== Starting Extruder + Gripper Test (gpiozero) ===")

# GPIO setup
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Extruder encoder setup
GPIO.setup(PIN_EXT_ENC_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(PIN_EXT_ENC_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(PIN_EXT_ENC_A, GPIO.BOTH, callback=ext_isr_a)
GPIO.add_event_detect(PIN_EXT_ENC_B, GPIO.BOTH, callback=ext_isr_b)

# Gripper encoder setup
GPIO.setup(PIN_GRIP_ENC_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(PIN_GRIP_ENC_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(PIN_GRIP_ENC_A, GPIO.BOTH, callback=grip_isr_a)
GPIO.add_event_detect(PIN_GRIP_ENC_B, GPIO.BOTH, callback=grip_isr_b)

# Extruder ESC setup (gpiozero)
ext_esc = Servo(
    PIN_EXT_ESC,
    initial_value=0,
    min_pulse_width=1100 / 1e6,
    max_pulse_width=1900 / 1e6,
    frame_width=1 / PWM_FREQ,
)

# Gripper ESC setup (gpiozero)
grip_esc = Servo(
    PIN_GRIP_ESC,
    initial_value=0,
    min_pulse_width=1100 / 1e6,
    max_pulse_width=1900 / 1e6,
    frame_width=1 / PWM_FREQ,
)

def set_ext_esc(pulse):
    val = esc_value(pulse)
    print(f"[EXT ESC] {pulse} µs → value {val:.3f}")
    ext_esc.value = val

def set_grip_esc(pulse):
    val = esc_value(pulse)
    print(f"[GRIP ESC] {pulse} µs → value {val:.3f}")
    grip_esc.value = val

# ─── ARM ESCs ───────────────────────────────────────────
print("\nArming ESCs...")

set_ext_esc(1900)
set_grip_esc(1900)
time.sleep(2)

set_ext_esc(1100)
set_grip_esc(1100)
time.sleep(2)

set_ext_esc(1500)
set_grip_esc(1500)
time.sleep(2)

print("ESCs armed\n")

# ─── TEST LOOP ──────────────────────────────────────────
try:
    cycle = 0
    while True:
        cycle += 1
        print(f"\n{'='*60}")
        print(f"CYCLE {cycle}")
        print(f"{'='*60}")

        # --- Test 1: Gripper CLOSE (approx 0.5 rev) ---
        print("\n[GRIPPER] CLOSE")
        start_grip = get_grip_ticks()
        set_grip_esc(CLOSE_FAST)
        time.sleep(0.35)
        set_grip_esc(STOP)
        end_grip = get_grip_ticks()
        print(f"[GRIPPER] ticks moved: {end_grip - start_grip}")
        time.sleep(0.3)

        # --- Test 2: Extruder FORWARD ---
        print("\n[EXTRUDER] FORWARD")
        start_ext = get_ext_ticks()
        set_ext_esc(FWD)
        time.sleep(1)
        set_ext_esc(STOP)
        end_ext = get_ext_ticks()
        print(f"[EXTRUDER] ticks moved: {end_ext - start_ext}")
        time.sleep(1)

        # --- Test 3: Extruder REVERSE ---
        print("\n[EXTRUDER] REVERSE")
        start_ext = get_ext_ticks()
        set_ext_esc(REV)
        time.sleep(1)
        set_ext_esc(STOP)
        end_ext = get_ext_ticks()
        print(f"[EXTRUDER] ticks moved: {end_ext - start_ext}")
        time.sleep(1)

        # --- Test 4: Gripper OPEN (approx 0.5 rev back) ---
        print("\n[GRIPPER] OPEN")
        start_grip = get_grip_ticks()
        set_grip_esc(OPEN_FAST)
        time.sleep(0.35)
        set_grip_esc(STOP)
        end_grip = get_grip_ticks()
        print(f"[GRIPPER] ticks moved: {end_grip - start_grip}")
        time.sleep(2)

except KeyboardInterrupt:
    print("\n\nStopping...")

finally:
    set_ext_esc(STOP)
    set_grip_esc(STOP)
    ext_esc.detach()
    ext_esc.close()
    grip_esc.detach()
    grip_esc.close()
    GPIO.cleanup()
    print("Clean shutdown")
