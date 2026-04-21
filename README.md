# AutoSauce — Automated Sauce Dispenser

Capstone project. A Raspberry Pi touchscreen kiosk that lets a user select
a sauce coverage level (light / medium / heavy), then automates a full
gantry + gripper + extruder + conveyor belt sequence to apply the sauce.

---

## System Architecture
```
Chromium (kiosk browser)
    │  HTTP on localhost:8080
FastAPI server  ←── main.py starts this
    │  Python function call
OrderManager    ←── runs motion sequence in background thread
    │
USB Serial connections:
    ├── vesc_gantry.py         →  NodeMCU ESP8266 (GantryCode)         — linear rail
    ├── arduino_controller.py  →  Arduino Mega    (PrintheadCode)      — gripper + extruder
    └── conveyor.py            →  Arduino Uno R3  (ConveyorHotdogCode) — conveyor + cylinder + lamp
```

### Microcontrollers

| Board | Firmware | USB Port (Windows) | USB Port (Pi) | Controls |
|-------|----------|--------------------|---------------|----------|
| HiLetGo NodeMCU ESP8266 | `GantryCode.ino` | COM3 | `/dev/ttyGANTRY` | Linear rail (encoder + ESC) |
| Arduino Mega 2560 | `PrintheadCode.ino` | COM4 | `/dev/ttyPRINTHEAD` | Gripper + Extruder (goBILDA ESCs) |
| Arduino Uno R3 | `ConveyorHotdogCode.ino` | COM5 | `/dev/ttyCONVEYOR` | Conveyor belt + Cylinder + Heat lamp |

---

## Project Structure
```
AutoSauce/
├── main.py                              # Entry point — run this
├── calibrate_gantry.py                 # Gantry calibration utility
├── test_gantry.py                      # Hardware test scripts
├── test_extruder_gpiozero.py
├── test_gripper_gpiozero.py
├── test_extruder_gripper_combined.py
├── ui/                                 # Static web UI (HTML/CSS/JS)
│   ├── index.html
│   ├── script.js                       # Calls the FastAPI endpoints
│   ├── style.css
│   └── scripts/
│       ├── launch.sh                   # Pi kiosk launcher (Chromium)
│       ├── launch.bat                  # Windows dev launcher
│       └── sauce-ui.service            # systemd unit for the UI
├── microcontroller code/               # Arduino/ESP8266 firmware
│   ├── GantryCode/
│   │   └── GantryCode.ino              # NodeMCU ESP8266 — linear rail
│   ├── PrintheadCode/
│   │   └── PrintheadCode.ino          # Arduino Mega — gripper + extruder
│   └── ConveyorHotdogCode/
│       └── ConveyorHotdogCode.ino     # Arduino Uno — conveyor + cylinder + lamp
└── pi/
    ├── main.py                         # Pi/Docker entry point
    ├── Dockerfile
    ├── api/
    │   ├── server.py                   # FastAPI endpoints
    │   ├── Dockerfile
    │   └── scripts/
    │       ├── launch-backend.sh
    │       └── sauce-backend.service   # systemd unit for the backend
    ├── ordering/
    │   ├── order_manager.py            # Queue + full motion sequence
    │   └── sauce_config.py             # All tunable physical values
    ├── motion/
    │   ├── vesc_gantry.py              # NodeMCU ESP8266 gantry driver (USB serial)
    │   ├── arduino_controller.py       # Shared serial singleton (Arduino Mega)
    │   ├── gripper.py                  # Gripper via ArduinoController
    │   ├── extruder.py                 # Extruder via ArduinoController
    │   ├── conveyor.py                 # Conveyor/cylinder/lamp (Arduino Uno, USB serial)
    │   ├── mock_drivers.py             # Fake drivers for dev/testing
    │   └── _shared.py                  # Shared PWM constants (Pi 5 gpiozero, Pi only)
    └── utils/
        └── logger.py                   # Shared logger
```

---

## Requirements

### Python version
```
Python 3.10 or higher
```

### Python packages

Install everything in one command:
```bash
pip install fastapi uvicorn pyserial
```

On Raspberry Pi (also installs GPIO support):
```bash
pip install fastapi uvicorn pyserial gpiozero lgpio --break-system-packages
```

### System packages (Raspberry Pi only)
```bash
sudo apt update
sudo apt install chromium unclutter
```
Note: the package is `chromium` not `chromium-browser` on modern Pi OS.
`unclutter` hides the mouse cursor on the touchscreen — optional but recommended.

---

## Running the project

### On Raspberry Pi (full kiosk mode)
Open two SSH terminals (see SSH section below).

**Terminal 1 — start the Python server:**
```bash
cd ~/AutoSauce
python main.py
```

**Terminal 2 — launch Chromium on the Pi's display:**
```bash
DISPLAY=:0 chromium http://localhost:8080/ui
```

Or use the launch script:
```bash
cd ~/AutoSauce/ui
bash launch.sh
```

### During development — skip launch.sh entirely
`launch.sh` is only needed to open Chromium on the Pi's own touchscreen.
While developing over SSH, just run the server and open the UI from your
Mac browser instead:
```bash
# On the Pi via SSH
cd ~/AutoSauce
python main.py
```

Then on your Mac open:
```
http://<pi-ip-address>:8080/ui
```

### On Windows (development / testing)
**Terminal 1 — start the Python server:**
```bash
cd AutoSauce
python main.py
```

**Terminal 2 — launch browser:**
```bash
cd AutoSauce/ui
launch.bat
```
Or just open `http://localhost:8080/ui` in any browser manually.

> `launch.bat` is Windows only. Never run it on the Pi.

### Headless (no browser, terminal only)
Useful for testing the motion sequence without a display:
```bash
cd AutoSauce
python -c "
import time
from pi.ordering.order_manager import OrderManager, OrderStatus
from pi.motion.mock_drivers import MockGantry, MockGripper, MockExtruder, MockConveyor

om = OrderManager(MockGantry(), MockGripper(), MockExtruder(), MockConveyor())
om.start()
order_id = om.submit_order('medium')
print('Submitted:', order_id)

while True:
    o = om.get_status(order_id)
    if o.status in (OrderStatus.DONE, OrderStatus.FAILED):
        print('Done:', o.status.name)
        break
    time.sleep(0.2)
"
```

---

## API Endpoints

Once `main.py` is running, these are available at `http://localhost:8080`:

| Method | Endpoint                  | Description                        |
|--------|---------------------------|------------------------------------|
| POST   | `/api/dispense`           | Submit a new order                 |
| GET    | `/api/status/{order_id}`  | Poll order progress                |
| GET    | `/api/levels`             | List valid coverage levels         |
| GET    | `/api/health`             | Check server is alive              |
| GET    | `/docs`                   | Auto-generated API docs (FastAPI)  |

### Example: submit an order
```bash
curl -X POST http://localhost:8080/api/dispense \
     -H "Content-Type: application/json" \
     -d '{"level": "medium"}'
```
Response:
```json
{ "order_id": "abc-123", "status": "QUEUED" }
```

### Example: check status
```bash
curl http://localhost:8080/api/status/abc-123
```
Response:
```json
{ "order_id": "abc-123", "status": "DONE", "error": null }
```

---

## Tuning the machine

All physical values are in one file: `pi/ordering/sauce_config.py`
```python
# Named positions along the gantry rail (mm from home end)
#   HOME end ──────────── DISPENSE ──────────── DOCK end
#   0 mm                  ~33 mm                ~355 mm
POSITIONS = {
    "home":     0,      # resting position between orders (home end of rail)
    "dispense": 33,     # start of sauce sweep (over the sandwich)
    "dock":     355,    # where the sauce bottle sits when not in use
}

# Gantry sweeps from POSITIONS["dispense"] to DISPENSE_SWEEP_END_MM
# while the extruder runs (~150 mm covers a full sandwich).
DISPENSE_SWEEP_END_MM = 150

# Per-level: gantry sweep speed (inches/sec). Extruder always runs at medium speed.
# Slower sweep → more sauce applied.
COVERAGE_PROFILES = {
    "light":  {"conveyor_speed": 80, "conveyor_ms": 3000},  # 4.0 in/s sweep
    "medium": {"conveyor_speed": 50, "conveyor_ms": 4500},  # 3.0 in/s sweep
    "heavy":  {"conveyor_speed": 25, "conveyor_ms": 6000},  # 1.0 in/s sweep
}
```

**Conveyor station positions** (set in `ConveyorHotdogCode.ino`):

| Station | Distance from home |
|---------|--------------------|
| HOTDOG  | 255 mm |
| HEAT    | 425 mm |
| SAUCE   | 739 mm |
| PICKUP  | 1020 mm |

**Tuning process:**
1. Adjust `POSITIONS` and `DISPENSE_SWEEP_END_MM` for the physical rail layout
2. Run a test order per level and adjust `conveyor_speed` and `conveyor_ms` until
   coverage looks right on a real sandwich
3. Sweep speeds are set in `order_manager.py` (`level_to_sweep_ips` dict) — tune
   these if sauce distribution is uneven

---

## Motion sequence

Each order executes these steps in order:

| Step | Controller | Action |
|------|-----------|--------|
| 3 | Conveyor Uno | Home conveyor belt |
| 4 | Conveyor Uno | Move to HOTDOG station (255 mm) |
| 5 | Conveyor Uno | Cylinder GRAB, wait 1 s, cylinder DROP |
| 6 | Conveyor Uno | Move to HEAT station (425 mm) |
| 7 | Conveyor Uno | Heat lamp ON for 10 s, then OFF |
| 8 | Conveyor Uno | Move to SAUCE station (739 mm) |
| 9 | Gantry ESP8266 | Move to dock (355 mm) — skipped if plunger already at contact |
| 10 | Printhead Mega | Gripper CLOSE (grab bottle) |
| 11 | Printhead Mega | Extruder MEETPLUNGER — skipped if plunger already at contact |
| 12 | Gantry ESP8266 | Move to dispense start (33 mm) |
| 13 | All | Zigzag ON + Extruder dispense + Gantry sweep to end (150 mm) — concurrent |
| 14 | Conveyor Uno | Zigzag STOP |
| 15 | — | Wait 5 s |
| 16 | Conveyor Uno | Move to PICKUP station (1020 mm) |
| 17 | Gantry ESP8266 | Return to dispense start (33 mm) |

---

## Hardware and drivers

### Driver summary

| Component | Driver file | Communicates with | Protocol |
|-----------|------------|-------------------|----------|
| Gantry rail | `vesc_gantry.py` | NodeMCU ESP8266 | USB serial, 115200 baud |
| Gripper | `gripper.py` via `arduino_controller.py` | Arduino Mega | USB serial, 9600 baud |
| Extruder | `extruder.py` via `arduino_controller.py` | Arduino Mega | USB serial, 9600 baud |
| Conveyor + cylinder + lamp | `conveyor.py` | Arduino Uno R3 | USB serial, 9600 baud |

`arduino_controller.py` is a singleton — the Mega serial port is opened once and
shared between `GPIOGripper` and `GPIOExtruder`.

### Switching between mock and real hardware

Two flags at the top of `main.py` control driver selection:

```python
USE_MOCK = True          # True = all mock drivers (no hardware needed)
USE_VESC_GANTRY = True   # True = real gantry even when USE_MOCK = True
                         # Override: USE_VESC_GANTRY=0 python main.py
```

`mock_drivers.py` provides `MockGantry`, `MockGripper`, `MockExtruder`, and
`MockConveyor`. These log every call and add small sleep delays so the sequence
runs realistically on a laptop.

### Installing Pi-only dependencies
```bash
pip install pyserial gpiozero lgpio --break-system-packages
```

---

## Adding more sauces (future)

1. Add dock positions to `sauce_config.py`:
```python
SAUCE_DOCKS = {
    "mayo":    0,
    "mustard": 50,
    "ketchup": 100,
}
```
2. Update the UI `index.html` to show sauce selection buttons
3. Update `order_manager.py` to accept a `sauce` parameter and look up
   its dock position from `SAUCE_DOCKS`

The motion sequence itself does not change.

---

## Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `No module named 'fastapi'` | Package not installed | `pip install fastapi uvicorn pyserial --break-system-packages` |
| `Address already in use` | Server already running on port 8080 | `pkill -f main.py` |
| `No module named 'pi'` | Wrong working directory | Run from `AutoSauce/` not inside `pi/` |
| `Unknown coverage level` | UI sent wrong level string | Check keys in `sauce_config.py` |
| `SerialException: could not open port COM3` | Gantry ESP8266 not connected or wrong port | Check USB connection; update `GANTRY_PORT` in `vesc_gantry.py` |
| `SerialException: could not open port COM4` | Arduino Mega not connected or wrong port | Check USB connection; update `_FIXED_PORT` in `arduino_controller.py` |
| `SerialException: could not open port COM5` | Arduino Uno not connected or wrong port | Check USB connection; update `CONVEYOR_PORT` in `conveyor.py` |
| `TimeoutError` during homing | Microcontroller not responding | Check firmware is uploaded; check serial baud rate |
| Chromium shows blank page | Server not running | Start `python main.py` first |
| `Directory 'ui' does not exist` | Server can't find UI folder | Make sure `ui/` folder exists with `index.html` inside |
| `chromium-browser: command not found` | Package renamed on new Pi OS | Run fix command below |
| `launch.sh: command not found` | Missing `./` prefix | Use `bash launch.sh` instead |
| Chromium opens but shows nothing | SSH session has no display | Run fix command below |

### Fix chromium package name in launch.sh
```bash
sed -i 's/chromium-browser/chromium/g' ~/AutoSauce/ui/launch.sh
```

### Fix Windows line endings in launch.sh
```bash
sudo apt install dos2unix -y
dos2unix ~/AutoSauce/ui/launch.sh
```

---

## SSH into the Raspberry Pi

### Credentials
```
username: saucemachine
password: me424
```

### 1. Find the Pi's IP address
On the Pi, run:
```bash
hostname -I
```
On school WiFi the IP may change between sessions. If SSH stops working,
plug a monitor into the Pi and run `hostname -I` again. Alternatively:
```bash
ssh saucemachine@raspberrypi.local
```
Its IP should be 172.28.85.104
### 2. Connect
```bash
ssh saucemachine@<ip>
```
First time only you'll see a fingerprint warning — type `yes` to continue.

### 3. End a session
```bash
exit
```
Or press `Ctrl+D`. If the session is frozen:
```
~.
```
Press `Enter`, then type `~` then `.` — immediately kills the connection.

### 4. Open a second terminal
Open a new Mac terminal tab (`Cmd + T`) and SSH again. Use one tab for
`python main.py` and the other for everything else.

### 5. Why Chromium won't open from SSH

When you SSH in, your session has no display attached. The Pi's desktop
runs on display `:0` as a separate world. Chromium doesn't know which
screen to draw on so it does nothing.
```
Pi's desktop (display :0)    ←── monitor sees this
SSH session (no display)     ←── your Mac terminal
```

**Permanent fix — run once:**
```bash
echo 'export DISPLAY=:0' >> ~/.bashrc
source ~/.bashrc
```

Now every SSH session automatically knows about the Pi's screen.

**Optional shortcut:**
```bash
echo 'alias chromepi="DISPLAY=:0 chromium http://localhost:8080/ui"' >> ~/.bashrc
source ~/.bashrc
```
Then just type `chromepi` to open the UI on the Pi's touchscreen.

> Note: a monitor must be physically connected and the desktop must be
> running for anything to appear.

### 6. Keep the server running after SSH disconnects
```bash
nohup python main.py &
```
Stop it later:
```bash
pkill -f main.py
```

### 7. tmux — optional, for unattended operation
tmux keeps terminal sessions alive even if WiFi drops. Use two SSH tabs
during development. Switch to tmux when the machine needs to run unattended.

**Install:**
```bash
sudo apt install tmux -y
```

| Shortcut | What it does |
|----------|-------------|
| `Ctrl+B` then `%` | Split pane vertically |
| `Ctrl+B` then `"` | Split pane horizontally |
| `Ctrl+B` then arrow key | Move between panes |
| `Ctrl+B` then `d` | Detach (keeps running in background) |
| `Ctrl+B` then `q` | Show pane numbers |

Reattach after disconnecting:
```bash
tmux attach
```

### 8. Transfer files to the Pi
From your Mac (not inside SSH):
```bash
scp -r AutoSauce/ saucemachine@<ip>:~/AutoSauce
```
For day-to-day work, push to GitHub and pull on the Pi:
```bash
cd ~/AutoSauce
git pull
```

---

## GitHub workflow
```
main      ← stable, tested, runs on the machine
develop   ← active development
```
```bash
# Start a new feature
git checkout develop
git checkout -b feature/gpio-gantry-driver

# When done
git checkout develop
git merge feature/gpio-gantry-driver

# When stable and tested on hardware
git checkout main
git merge develop
```

---

## Running with systemd services and Docker (Recommended for deployment)

This project now supports running the backend (API server) and UI (Chromium kiosk) as separate services using systemd and Docker.

### 1. Build the backend Docker image
```bash
cd ~/AutoSauce
sudo docker build -t saucebot-backend .
```

### 2. Make launch scripts executable
```bash
chmod +x ui/launch-ui.sh ui/launch-backend.sh
```

### 3. Copy and enable the systemd service files
```bash
sudo cp ui/sauce-backend.service /etc/systemd/system/
sudo cp ui/sauce-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sauce-backend
sudo systemctl enable sauce-ui
```

### 4. Start the services
```bash
sudo systemctl start sauce-backend
sudo systemctl start sauce-ui
```

- The backend will run in Docker and listen on port 8080.
- The UI will launch Chromium in kiosk mode on the Pi's display.

### 5. Checking logs
- For backend (Docker):
  ```bash
  sudo docker logs saucebot-backend
  sudo docker logs -f saucebot-backend  # follow live
  ```
- For systemd services:
  ```bash
  sudo journalctl -u sauce-backend -e
  sudo journalctl -u sauce-ui -e
  ```

### 6. Stopping and disabling services
```bash
sudo systemctl stop sauce-backend
sudo systemctl stop sauce-ui
sudo systemctl disable sauce-backend
sudo systemctl disable sauce-ui
```
