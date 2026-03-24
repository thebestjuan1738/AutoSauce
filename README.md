# AutoSauce — Automated Sauce Dispenser

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
AutoSauce/
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

Install everything in one command:
```bash
pip install fastapi uvicorn
```

On Raspberry Pi if you get a permissions error:
```bash
pip install fastapi uvicorn --break-system-packages
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
| `No module named 'fastapi'` | Package not installed | `pip install fastapi uvicorn --break-system-packages` |
| `Address already in use` | Server already running on port 8080 | `pkill -f main.py` |
| `No module named 'pi'` | Wrong working directory | Run from `AutoSauce/` not inside `pi/` |
| `Unknown coverage level` | UI sent wrong level string | Check keys in `sauce_config.py` |
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
