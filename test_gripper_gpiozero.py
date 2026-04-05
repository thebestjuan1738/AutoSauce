import time
import threading
import RPi.GPIO as GPIO
from gpiozero import Device, Servo
from gpiozero.pins.lgpio import LGPIOFactory

# ─── Use lgpio (required for Pi 5) ──────────────────────
Device.pin_factory = LGPIOFactory()

# ─── CONFIG ─────────────────────────────────────────────
PIN_ESC = 12
PIN_ENC_A = 16
PIN_ENC_B = 20

PWM_FREQ = 50

# ESC pulse widths (µs)
STOP = 1500
OPEN_FAST = 1650
CLOSE_FAST = 1350

# ─── GLOBAL STATE ───────────────────────────────────────
ticks = 0
lock = threading.Lock()

# ─── HELPER ─────────────────────────────────────────────
def esc_value(pulse_us):
    return (pulse_us - 1500) / 400.0

# ─── ENCODER CALLBACKS ──────────────────────────────────
def isr_a(channel):
    global ticks
    a = GPIO.input(PIN_ENC_A)
    b = GPIO.input(PIN_ENC_B)
    with lock:
        if a == b:
            ticks += 1
        else:
            ticks -= 1

def isr_b(channel):
    global ticks
    a = GPIO.input(PIN_ENC_A)
    b = GPIO.input(PIN_ENC_B)
    with lock:
        if a != b:
            ticks += 1
        else:
            ticks -= 1

def get_ticks():
    with lock:
        return ticks

# ─── SETUP ──────────────────────────────────────────────
print("=== Starting Gripper Test (gpiozero) ===")

# Encoder setup
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

GPIO.setup(PIN_ENC_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(PIN_ENC_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)

GPIO.add_event_detect(PIN_ENC_A, GPIO.BOTH, callback=isr_a)
GPIO.add_event_detect(PIN_ENC_B, GPIO.BOTH, callback=isr_b)

# ESC setup (gpiozero)
esc = Servo(
    PIN_ESC,
    initial_value=0,
    min_pulse_width=1100 / 1e6,
    max_pulse_width=1900 / 1e6,
    frame_width=1 / PWM_FREQ,
)

def set_esc(pulse):
    val = esc_value(pulse)
    print(f"[ESC] {pulse} µs → value {val:.3f}")
    esc.value = val

# ─── ARM ESC ────────────────────────────────────────────
print("\nArming ESC...")

set_esc(1900)
time.sleep(2)

set_esc(1100)
time.sleep(2)

set_esc(1500)
time.sleep(2)

print("ESC armed\n")

# ─── TEST LOOP ──────────────────────────────────────────
try:
    while True:
        print("\n--- OPEN ---")
        start_ticks = get_ticks()

        set_esc(OPEN_FAST)
        time.sleep(1)
        set_esc(STOP)

        end_ticks = get_ticks()
        print(f"[ENCODER] ticks moved: {end_ticks - start_ticks}")

        time.sleep(2)

        print("\n--- CLOSE ---")
        start_ticks = get_ticks()

        set_esc(CLOSE_FAST)
        time.sleep(1)
        set_esc(STOP)

        end_ticks = get_ticks()
        print(f"[ENCODER] ticks moved: {end_ticks - start_ticks}")

        time.sleep(3)

except KeyboardInterrupt:
    print("\nStopping...")

finally:
    set_esc(STOP)
    esc.detach()
    esc.close()
    GPIO.cleanup()
    print("Clean shutdown")