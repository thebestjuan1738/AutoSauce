# AutoSauce

Raspberry Pi-based automated sauce dispensing system. A touchscreen kiosk that lets users select sauce coverage level (light/medium/heavy), then automates a multi-actuator sequence to dispense sauce onto items on a conveyor belt.

## Important Guidelines

- **DO NOT modify `main.py`** - Changes require Docker image rebuild. Avoid unless explicitly told.
- **`pi/ordering/order_manager.py`** - Main orchestration program that controls all sequencing. Make changes here for workflow modifications.
- **`pi/motion/` scripts** - Control subclasses that send serial commands to slave Arduinos and ESP32s. Modify these for hardware control changes.

## Tech Stack

- **Backend**: Python 3.10+, FastAPI, Uvicorn
- **Frontend**: Vanilla HTML/CSS/JS served via FastAPI static files
- **Hardware Control**:
  - Arduino/NodeMCU over USB serial for motion control
  - RPi.GPIO/gpiozero for PWM motor control
  - PyVESC for VESC gantry controller
- **Deployment**: Docker + systemd services on Raspberry Pi

## Directory Structure

```
AutoSauce/
├── main.py                    # Entry point - starts FastAPI server & OrderManager
├── calibrate_gantry.py        # Interactive gantry calibration utility
├── autosauce_testing.ino      # Arduino firmware for gantry control
├── pi/                        # Backend Python package
│   ├── api/
│   │   └── server.py          # FastAPI endpoint definitions
│   ├── ordering/
│   │   ├── order_manager.py   # Queue + motion sequence orchestration
│   │   └── sauce_config.py    # All tunable physical parameters
│   ├── motion/
│   │   ├── vesc_gantry.py     # Gantry driver (VESC + Arduino)
│   │   ├── gripper.py         # Gripper driver (PWM + Arduino)
│   │   ├── extruder.py        # Extruder driver (PWM + Arduino)
│   │   ├── conveyor.py        # Conveyor driver (PWM)
│   │   ├── arduino_controller.py  # Serial communication singleton
│   │   └── mock_drivers.py    # Fake drivers for testing/dev
│   └── utils/
│       └── logger.py          # Centralized logging with in-memory buffer
└── ui/                        # Frontend static files
    ├── index.html             # Main kiosk interface
    ├── script.js              # UI logic & API calls
    └── style.css              # Styling & animations
```

## Running the Project

```bash
# Start the server (from repo root)
python main.py

# Open UI in browser
# http://localhost:8080/ui
```

Toggle `USE_MOCK = True/False` in `main.py` to switch between mock and real hardware drivers.

## Key Configuration

All tunable parameters are in `pi/ordering/sauce_config.py`:

```python
POSITIONS = {
    "dock": 355,      # Sauce dispenser rest position
    "home": 0,        # Resting position between orders
    "dispense": 20,   # Over conveyor belt
}

COVERAGE_PROFILES = {
    "light":  {"conveyor_speed": 80, "conveyor_ms": 3000},
    "medium": {"conveyor_speed": 50, "conveyor_ms": 4500},
    "heavy":  {"conveyor_speed": 25, "conveyor_ms": 6000},
}
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/dispense` | Submit order `{"level": "medium"}` |
| GET | `/api/status/{order_id}` | Poll order progress |
| GET | `/api/levels` | List valid coverage levels |
| GET | `/api/logs` | Get recent log entries |
| GET | `/api/health` | Health check |
| POST | `/api/manual/home-gripper` | Home gripper |
| POST | `/api/manual/home-extruder` | Home extruder |
| POST | `/api/manual/move-gantry/{location}` | Move gantry to named position |

## Motion Sequence

Orders follow this 10-step sequence in `order_manager.py`:

1. Gantry → dock
2. Gripper close (grab bottle)
3. Gantry → dispense position
4. Extruder → meet plunger
5-6. Concurrent: Conveyor runs + Extruder dispenses + Gantry sweeps
7-8. Concurrent: Extruder retracts + Gantry returns to dock
9. Gripper open (release bottle)
10. Gantry → home

## Key Patterns

- **Dependency Injection**: `OrderManager` receives driver objects, enabling mock testing
- **Thread-safe Queue**: Orders processed one at a time by background worker
- **Arduino Singleton**: `ArduinoController` manages serial port with thread locking
- **Mock Drivers**: All hardware has mock implementations for dev without hardware

## GPIO Pins (BCM)

```python
PIN_GRIPPER  = 23
PIN_EXTRUDER = 18
PIN_CONVEYOR = 24
```

## Driver Interfaces

All drivers follow consistent interfaces:

```python
# Gantry
move_to(position_mm: int) → None
home() → None
get_position_mm() → float

# Gripper/Extruder
home() → None
close()/open() or dispense()/retract()

# Conveyor
start(speed: int) → None  # 0-100
stop() → None
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

### Docker Build (if needed)

```bash
sudo docker build -t saucebot-backend .
sudo docker run -d --name saucebot --device /dev/ttyACM0 -p 8080:8080 saucebot-backend
```

## Common Issues

| Error | Fix |
|-------|-----|
| `No module named 'fastapi'` | `pip install fastapi uvicorn` |
| `Address already in use` | `pkill -f main.py` |
| `No module named 'pi'` | Run from `AutoSauce/` root directory |
| Blank page in browser | Ensure `python main.py` is running |
