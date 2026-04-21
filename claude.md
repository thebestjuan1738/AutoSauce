# AutoSauce

Raspberry Pi-based automated sauce dispensing system. A touchscreen kiosk that lets users select sauce coverage level (light/medium/heavy), then automates a multi-actuator sequence to dispense sauce onto hotdogs on a conveyor belt.

## Important Guidelines

- **DO NOT modify `main.py`** - Changes require Docker image rebuild. Avoid unless explicitly told.
- **`pi/ordering/order_manager.py`** - Main orchestration program that controls all sequencing. Make changes here for workflow modifications.
- **`pi/motion/` scripts** - Control subclasses that send serial commands to slave Arduinos and ESP32s. Modify these for hardware control changes.

## Hardware Architecture

The system uses 3 microcontrollers communicating over USB serial:

| Controller | Hardware | Port | Baud | Purpose |
|------------|----------|------|------|---------|
| **GantryCode** | NodeMCU ESP8266 | /dev/ttyUSB0 | 115200 | Linear gantry positioning |
| **PrintheadCode** | Arduino Mega | /dev/ttyACM0 | 115200 | Gripper + Extruder |
| **ConveyorHotdogCode** | Arduino Uno | /dev/ttyACM1 | 9600 | Conveyor + Cylinder + Lamp |

## Microcontroller Commands

### GantryCode (ESP8266) - `vesc_gantry.py`

| Command | Response | Description |
|---------|----------|-------------|
| `GOTO<inches>` | `GOTO COMPLETE` | Move to position |
| `DOCK` | `DOCK COMPLETE` | Move to dock (14.0 in) |
| `SAUCE<speed>` | `SAUCE COMPLETE` | Run sauce dispensing sequence |
| `ZERO` | `HOMING COMPLETE` | Run homing routine |
| `POS` | `[POS] X.XXX in` | Query current position |
| `STOP` | `STOPPED` | Emergency stop |

### PrintheadCode (Mega) - `gripper.py` + `extruder.py`

| Command | Response | Description |
|---------|----------|-------------|
| `HOMEGRAB` | `HOMEGRAB_DONE` | Home gripper |
| `GRAB` | `GRAB_DONE` | Close gripper (grab bottle) |
| `RELEASE` | `RELEASE_DONE` | Open gripper (release bottle) |
| `HOMEEXT` | `HOMEEXT_DONE` | Home extruder |
| `MEETPLUNGER` | `PLUNGER_DONE` | Drive to plunger contact |
| `EXTRUDESLOW/MED/FAST` | `EXTRUDING` | Start dispensing |
| `STOPEXT` | `STOPEXT_DONE` | Stop extruder |
| `OPENEXT` | `OPENEXT_DONE` | Retract extruder |

### ConveyorHotdogCode (Uno) - `conveyor.py`

| Command | Response | Description |
|---------|----------|-------------|
| `HOME` | `HOME_DONE` | Zero position |
| `HOTDOG` | `MOVE_DONE:HOTDOG` | Move to hotdog station (268mm) |
| `HEAT` | `MOVE_DONE:HEAT` | Move to heat station (438mm) |
| `SAUCE` | `MOVE_DONE:SAUCE` | Move to sauce station (739mm) |
| `PICKUP` | `MOVE_DONE:PICKUP` | Move to pickup station (1020mm) |
| `ZIGZAG` | - | Start zigzag oscillation |
| `ZIGZAGSTOP` | `MOVE_DONE:ZIGZAG` | Stop zigzag |
| `GRAB` | `CYL_DONE:GRAB` | Cylinder to grab position |
| `DROP` | `CYL_DONE:DROP` | Cylinder to drop position |
| `LAMPON` | `LAMP_DONE:ON` | Turn on heat lamp |
| `LAMPOFF` | `LAMP_DONE:OFF` | Turn off heat lamp |

## Full Motion Sequence

Orders follow this 16-step sequence in `order_manager.py`:

**Phase 1: Hotdog Loading & Heating**
1. Conveyor → HOME
2. Conveyor → HOTDOG station
3. Cylinder → GRAB (pick up hotdog)
4. Conveyor → HEAT station
5. Lamp ON → wait 3s → Lamp OFF
6. Cylinder → DROP (place hotdog)
7. Conveyor → SAUCE station

**Phase 2: Sauce Dispensing**
8. Gantry → dock position
9. Gripper → GRAB (grab sauce bottle)
10. Gantry → dispense position
11. Extruder → MEETPLUNGER
12. Concurrent: Extruder dispenses + Gantry sweeps + Conveyor zigzags

**Phase 3: Cleanup**
13. Extruder retract + Gantry → dock (simultaneous)
14. Gripper → RELEASE
15. Gantry → home
16. Conveyor → PICKUP station

## Directory Structure

```
AutoSauce/
├── main.py                    # Entry point - starts FastAPI server & OrderManager
├── microcontroller code/      # Arduino firmware for all 3 controllers
│   ├── GantryCode/           # ESP8266 gantry controller
│   ├── PrintheadCode/        # Mega gripper + extruder
│   └── ConveyorHotdogCode/   # Uno conveyor + cylinder + lamp
├── pi/                        # Backend Python package
│   ├── api/server.py          # FastAPI endpoints
│   ├── ordering/
│   │   ├── order_manager.py   # Main orchestration (16-step sequence)
│   │   └── sauce_config.py    # Tunable parameters
│   ├── motion/
│   │   ├── vesc_gantry.py     # Gantry driver (ESP8266)
│   │   ├── gripper.py         # Gripper driver (Mega)
│   │   ├── extruder.py        # Extruder driver (Mega)
│   │   ├── conveyor.py        # Conveyor driver (Uno)
│   │   ├── arduino_controller.py  # Serial singleton for Mega
│   │   └── mock_drivers.py    # Fake drivers for testing
│   └── utils/logger.py
└── ui/                        # Frontend (HTML/CSS/JS)
```

## Running the Project

```bash
# Start the server (from repo root)
python main.py

# Open UI in browser
# http://localhost:8080/ui
```

Toggle `USE_MOCK = True/False` in `main.py` to switch between mock and real hardware.

## Key Configuration

All tunable parameters are in `pi/ordering/sauce_config.py`:

```python
POSITIONS = {
    "dock": 355,      # Sauce dispenser rest position (mm)
    "home": 0,        # Resting position between orders
    "dispense": 20,   # Over conveyor belt
}

COVERAGE_PROFILES = {
    "light":  {"conveyor_speed": 80, "conveyor_ms": 3000},
    "medium": {"conveyor_speed": 50, "conveyor_ms": 4500},
    "heavy":  {"conveyor_speed": 25, "conveyor_ms": 6000},
}
```

## Raspberry Pi Deployment

```bash
# SSH into the Raspberry Pi
ssh saucemachine@172.28.85.104
# Password: me424

# Navigate to the project
cd AutoSauce/

# Pull latest changes
git pull

# Restart the backend service
sudo systemctl restart sauce-backend

# View logs
docker logs sauce-backend
```

## Driver Interfaces

```python
# Gantry (vesc_gantry.py)
move_to(position_mm: int, max_duty: float = 1.0) → None
home() → None
get_position_mm() → float

# Gripper (gripper.py)
home() → None
close() → None   # GRAB
open() → None    # RELEASE

# Extruder (extruder.py)
home() → None
meet_plunger() → None
dispense(speed: str = "medium") → None  # "slow", "medium", "fast"
stop_dispense() → None
retract() → None

# Conveyor (conveyor.py)
home() → None
move_to_station(station: str) → None  # "hotdog", "heat", "sauce", "pickup"
start_zigzag() → None
stop_zigzag() → None
cylinder_grab() → None
cylinder_drop() → None
lamp_on() → None
lamp_off() → None
```

## Common Issues

| Error | Fix |
|-------|-----|
| `No module named 'fastapi'` | `pip install fastapi uvicorn` |
| `Address already in use` | `pkill -f main.py` |
| `No module named 'pi'` | Run from `AutoSauce/` root directory |
| Blank page in browser | Ensure `python main.py` is running |
| Arduino timeout | Check USB connections, verify correct port |
