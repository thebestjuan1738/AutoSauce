# SauceBot — Automated Sauce Dispenser

Capstone project. A Raspberry Pi touchscreen kiosk that lets a user select
a sauce coverage level (light / medium / heavy), then automates a full
gantry + gripper + extruder + conveyor belt sequence to apply the sauce.

---

## System Architecture

```
Chromium (kiosk browser)
    |  HTTP on localhost:8080
FastAPI server  ←── main.py starts this
    |  Python function call
OrderManager    ←── runs motion sequence in background thread
    |
Mock drivers (now) / GPIO drivers (when hardware arrives)
    |
Gantry · Gripper · Extruder · Conveyor
    |  Serial (future)
Arduino (optional, add later if real-time control is needed)
```

---

## Project Structure

```
saucebot/
├── main.py                        # Entry point — run this
├── ui/                            # Static web UI (HTML/CSS/JS)
│   ├── index.html
│   ├── script.js                  # Calls the FastAPI endpoints
│   ├── style.css
│   ├── launch.sh                  # Pi launcher (Chromium kiosk)
│   └── launch.bat                 # Windows test launcher
└── pi/
    ├── api/
    │   └── server.py              # FastAPI endpoints
    ├── ordering/
    │   ├── order_manager.py       # Queue + motion sequence
    │   └── sauce_config.py        # All tunable physical values
    ├── motion/
    │   └── mock_drivers.py        # Fake hardware for dev/testing
    └── utils/
        └── logger.py              # Shared logger
```

---

## Requirements

### Python version
```
Python 3.10 or higher
```

### Python packages
```
fastapi
uvicorn
```

Install everything in one command:
```bash
pip install fastapi uvicorn
```

On Raspberry Pi if you get a permissions error:
```bash
pip install fastapi uvicorn --break-system-packages
```

### System packages (Raspberry Pi only)
Chromium is used as the kiosk browser. Install if not already present:
```bash
sudo apt update
sudo apt install chromium-browser unclutter
```
`unclutter` hides the mouse cursor on the touchscreen — optional but recommended.

---

## Running the project

### On Raspberry Pi (full kiosk mode)
Open two terminals.

**Terminal 1 — start the Python server:**
```bash
cd ~/saucebot
python main.py
```

**Terminal 2 — launch Chromium kiosk:**
```bash
cd ~/saucebot/ui
bash launch.sh
```

Or to have everything start automatically at boot, follow the systemd
instructions at the bottom of `ui/launch.sh`.

### On Windows (development / testing)
**Terminal 1 — start the Python server:**
```bash
cd saucebot
python main.py
```

**Terminal 2 — launch browser:**
```bash
cd saucebot/ui
launch.bat
```
Or just open `http://localhost:8080/ui` in any browser manually.

### Headless (no browser, terminal only)
Useful for testing the motion sequence without a display:
```bash
cd saucebot
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
# Named positions along the rail (mm from dock end)
POSITIONS = {
    "dock":     0,      # where sauce dispenser sits at rest
    "home":     500,    # gantry resting position between orders
    "dispense": 1000,   # over the conveyor belt
}

# Gripper motor run times
GRIPPER = {
    "close_ms": 500,
    "open_ms":  500,
}

# Per-level: belt speed, extruder duration, belt duration
COVERAGE_PROFILES = {
    "light":  {"conveyor_speed": 80, "extrude_ms": 600, "conveyor_ms": 3000},
    "medium": {"conveyor_speed": 50, "extrude_ms": 600, "conveyor_ms": 4500},
    "heavy":  {"conveyor_speed": 25, "extrude_ms": 600, "conveyor_ms": 6000},
}
```

**Tuning process:**
1. Adjust `POSITIONS` once the rail is assembled and measured
2. Adjust `GRIPPER` close/open times so the gripper fully engages without stalling
3. Run a test order per level and adjust `conveyor_speed` and `conveyor_ms` until
   coverage looks right on a real sandwich

---

## Swapping in real hardware drivers

When the hardware arrives, create `pi/motion/gpio_drivers.py` with real
implementations of the four driver classes:

```python
class GPIOGantry:
    def move_to(self, position_mm: int) -> None: ...

class GPIOGripper:
    def close(self, duration_ms: int) -> None: ...
    def open(self, duration_ms: int) -> None: ...

class GPIOExtruder:
    def dispense(self, duration_ms: int) -> None: ...

class GPIOConveyor:
    def start(self, speed: int) -> None: ...
    def stop(self) -> None: ...
```

Then in `main.py`, change one block:
```python
# Before (mock):
from pi.motion.mock_drivers import MockGantry, MockGripper, MockExtruder, MockConveyor

# After (real hardware):
from pi.motion.gpio_drivers import GPIOGantry, GPIOGripper, GPIOExtruder, GPIOConveyor
```

Nothing else changes.

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
| `No module named 'fastapi'` | Package not installed | `pip install fastapi uvicorn` |
| `Address already in use` | Server already running on port 8080 | Kill the old process: `pkill -f main.py` |
| `No module named 'pi'` | Wrong working directory | Run from the `saucebot/` folder, not inside `pi/` |
| `Unknown coverage level` | UI sent wrong level string | Check `COVERAGE_PROFILES` keys in `sauce_config.py` |
| Chromium shows blank page | Server not running | Start `python main.py` first |

---

 
## SSH into the Raspberry Pi
 
 
### 1. Enable SSH on the Pi (Already Done)
On the Pi itself, open a terminal and run:
```bash
sudo raspi-config
```
Navigate to: **Interface Options → SSH → Enable**
 
Or the quick way without opening raspi-config:
```bash
sudo systemctl enable ssh
sudo systemctl start ssh
```
 
### 2. Find the Pi's IP address 
On the Pi, run:
```bash
hostname -I
```
It should return an address like 172.28.85.104
 
 
### 3. Connect from your computer
On Windows (PowerShell) / Mac / Linux, run:
```bash
ssh pi@172.28.85.104
```
Username: saucemachine
Password: me424
 
You'll see this warning the very first time — this is normal, type `yes`:
```
The authenticity of host '172.28.85.104' can't be established.
Are you sure you want to continue connecting? (yes/no): yes
```
 
### 6. Useful SSH tips
 
Keep the Pi's IP stable — assign it a static IP in your router settings
so it doesn't change between reboots. Look for "DHCP reservation" or
"static lease" in your router admin page and bind the Pi's MAC address
to a fixed IP.
 
Run the server in the background so it keeps going after you close SSH:
```bash
nohup python main.py &
```
To stop it later:
```bash
pkill -f main.py
```
 
---

## GitHub recommended workflow

```
main      ← stable, tested, runs on the machine
develop   ← active development, merge into main when working
```

```bash
# Start a new feature
git checkout develop
git checkout -b feature/gpio-gantry-driver

# When done and tested
git checkout develop
git merge feature/gpio-gantry-driver

# When develop is stable and tested on hardware
git checkout main
git merge develop
```
