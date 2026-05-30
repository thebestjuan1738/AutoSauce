# AutoSauce

**ME424 Senior Capstone — Automated Sauce Dispenser**

---

## 1. Project Overview

AutoSauce is an automated sauce-dispensing machine built for a food-service context. A user walks up to a 7-inch touchscreen, selects a coverage level — light, medium, or heavy — presses start, and the machine handles the rest: it picks up a hotdog, moves it through a heating station, applies sauce with a motorized printhead on a linear gantry rail, and delivers it to a pickup window. No operator involvement after the button press.

I built this as my senior capstone at [University] with a six-person team in ME424. My contributions were the physical machine — frame, conveyor, electronics, and wiring — and the embedded firmware that controls the gantry rail, the conveyor belt, and the cylinder positioner. A teammate designed and assembled the sauce printhead. The team handled the Raspberry Pi software: the touchscreen UI, the REST API backend, and the deployment pipeline. My job at the software boundary was to extend and adapt the existing Python driver layer so it correctly drove my firmware through the full dispensing sequence.

---

## 2. Technical Architecture

The system has four layers. My work covers the two on the right; the team owned the two on the left.

```
┌──────────────────────┐     ┌──────────────────────┐
│  Touchscreen UI      │     │  FastAPI Backend      │
│  (vanilla HTML/JS)   │────▶│  + OrderManager       │
│                      │     │  (Python, Pi)         │
│  [teammates' work]   │     │  [teammates' work,    │
│                      │     │   modified by me for  │
│                      │     │   firmware integration]│
└──────────────────────┘     └──────────┬───────────┘
                                        │ USB serial (3 connections)
               ┌────────────────────────┼────────────────────────┐
               │                        │                        │
    ┌──────────▼──────────┐  ┌──────────▼──────────┐  ┌─────────▼──────────┐
    │   GantryCode        │  │  PrintheadCode       │  │  ConveyorHotdog-   │
    │   NodeMCU ESP8266   │  │  Arduino Mega        │  │  Code  Uno         │
    │   115200 baud       │  │  115200 baud         │  │  9600 baud         │
    │                     │  │                      │  │                    │
    │   Linear rail       │  │  Gripper motor       │  │  Conveyor belt     │
    │   BLDC + ESC        │  │  Extruder motor      │  │  Rotary cylinder   │
    │   Quadrature enc.   │  │  Plunger sensor      │  │  Heat lamp         │
    │   Limit switch      │  │                      │  │  Encoder + limit   │
    │                     │  │  [teammate firmware, │  │                    │
    │   [my firmware]     │  │   modified by me]    │  │  [my firmware]     │
    └─────────────────────┘  └──────────────────────┘  └────────────────────┘
```

The Python backend runs on a Raspberry Pi 4 and coordinates all three boards over USB serial using a text-based command protocol: the Pi sends a command string (e.g., `SAUCE5.0`), and the firmware responds with a completion string (e.g., `SAUCE COMPLETE`) when done. The `OrderManager` runs a single background thread, pulling orders from a queue and stepping through the 16-stage dispensing sequence — conveyor homing, hotdog loading, heating, gantry positioning, concurrent sauce dispense, and delivery to pickup. The team built the queue, the FastAPI layer, and the UI; I extended the Python driver files for the gantry and conveyor to be robust against USB re-enumeration and port ambiguity, and I modified the orchestration to correctly sequence and synchronize my firmware.

---

## 3. Key Technical Challenges & Solutions

### A. Gantry Position Control from Scratch

The gantry is a custom linear rail driven by a brushless motor through an ESC (Electronic Speed Controller). ESCs take a PWM signal — 1000 µs full reverse, 1500 µs stop, 2000 µs full forward — and don't expose position feedback natively. I needed sub-centimeter repeatable positioning over ~350 mm of travel.

I wired a quadrature encoder to the carriage (2053.67 counts/inch) and wrote the full control loop in GantryCode.ino. The architecture is a cascaded controller: an outer position loop runs every 20 ms, computing a speed setpoint from position error; an inner speed loop runs every 5 ms, computing an ESC pulse width from speed error. I instrumented it with a `LOG` command that streams live CSV telemetry — timestamp, position, target, error, speed, ESC pulse — for offline tuning in a spreadsheet. The current build runs a simplified bang-bang controller (fixed pulse magnitude per direction) while I tune the PID gains against that telemetry data; the full cascaded PID structure is in the code and ready to activate once gains are validated.

### B. Repeatable Homing

A limit switch at the home end of the rail gives an absolute reference, but single-phase homing — reverse to contact, zero the encoder — left ~2 mm of run-to-run scatter because the switch contact point depends on approach speed and switch hysteresis.

Two-phase homing eliminates this. Phase 1: reverse at full power until the switch closes (coarse find). Phase 2: drive forward slowly until the switch opens, then zero the encoder. The switch release edge is a physically stable point — the same place every time, independent of approach direction. This brought repeatability to within the encoder's resolution.

### C. Concurrent Dispense Synchronization

Sauce coverage depends on the extruder and the gantry sweep starting at the same moment. The gantry's `SAUCE` command runs a two-phase sequence: Phase 1 reverses the carriage to the sweep start position (1.90 in from home); Phase 2 drives it forward to the sweep end (6.30 in) at the caller-specified speed. The extruder must start when Phase 2 begins — not during Phase 1, and not offset by a fixed sleep.

The firmware broadcasts the string `DISPENSING` at the exact moment Phase 2 starts. I extended the Python `sauce()` method in `vesc_gantry.py` to block on that string: when it arrives, the method fires an `on_dispense_start` callback, which starts the extruder. The gantry driver continues blocking until it receives `SAUCE COMPLETE`. A `finally` block around the whole call guarantees the extruder stops regardless of whether the gantry completes or times out — no path leaves the motor running unattended.

```python
def on_dispense_start():
    extruder.dispense(speed="medium")

gantry.sauce(sweep_speed_ips=5.0, on_dispense_start=on_dispense_start)
# finally: extruder.stop_dispense()
```

### D. Rotary Cylinder Angle Control

The conveyor's rotary cylinder uses a servo with PWM pulse-width position feedback — the feedback line pulses proportionally to angle (0–360°). I needed reliable positioning at two angles: 274° (GRAB) and 197° (DROP).

The problem with naive PID on a circular range: if the carriage is at 5° and the target is 355°, the error computes as −350°, commanding a nearly full rotation in the wrong direction. The correct move is +10°. I implemented angle wrapping in the firmware: error = `((target − current + 540) % 360) − 180`, which always gives the shortest-path signed error in [−180°, +180°]. The PID acts on that, with a 2° dead-band around the setpoint to prevent hunting. Outlier filtering on the feedback pulse (rejecting readings more than 15° from the last valid sample) keeps noise from destabilizing the loop.

---

## 4. Skills Demonstrated

**Mechanical prototyping & fabrication**
- Designed and built the machine frame, conveyor system, and all mechanical assemblies (excluding the printhead, which a teammate designed)
- Selected and integrated motors, encoders, limit switches, relays, and sensors

**Embedded firmware (C++ / Arduino)**
- Wrote GantryCode.ino (999 lines): cascaded PID architecture, quadrature encoder ISRs, two-phase homing, two-phase sauce sweep state machine, live telemetry streaming
- Wrote ConveyorHotdogCode.ino (515 lines): relay-based conveyor drive with encoder position feedback, PID-controlled rotary cylinder with angle wrapping, non-blocking zigzag oscillation state machine, heat lamp control
- Adapted PrintheadCode.ino (teammate's original) for assembly integration with the full machine sequence

**Serial command protocol & multi-board integration**
- Designed and implemented text-based command/response protocol across three USB serial connections (ESP8266 at 115200, Mega at 115200, Uno at 9600)
- Handled boot sequencing, timeout detection, and multi-board coordination from firmware side

**Python orchestration integration**
- Extended `vesc_gantry.py` and `conveyor.py` with robust USB port auto-detection (VID/PID matching with command-probe fallback), reconnection logic for USB re-enumeration, and the `DISPENSING` callback mechanism
- Modified `order_manager.py` to correctly sequence and synchronize the full 16-step motion sequence against my firmware's command/response protocol

---

## 5. What I Learned

The biggest surprise was how much of embedded systems work is debugging timing that doesn't show up in simulation. Encoder counts drop when the ISR is interrupted; serial responses arrive in fragments; a limit switch bounces differently at different temperatures. Instrumentation — specifically the live CSV telemetry I built into the gantry firmware — turned guesswork into data. Seeing position, speed, and ESC pulse on the same timeline made it immediately obvious when the controller was overshooting versus when the ESC was simply too slow to respond.

I also learned what it costs to not define a clean interface early. My firmware and the Python orchestration were developed somewhat in parallel, and every time one side changed, the other broke in a way that was hard to debug over serial. We fixed this by treating the command/response strings as a contract and stabilizing them before tuning anything. After that, both sides could evolve independently. That discipline — defining the interface before the implementation — is something I'll carry into every project with a hardware/software boundary.

---

## 6. Demo & Media

**Video**

`[Video embed — machine running a full light/medium/heavy cycle]`

**Photos**

`[Photo 1: Full machine — frame, conveyor, gantry rail, electronics enclosure]`
*Full assembly. Conveyor belt runs left to right; gantry rail crosses above the sauce zone.*

`[Photo 2: Gantry carriage close-up — motor, encoder, limit switch]`
*Gantry carriage with brushless motor, quadrature encoder, and home limit switch.*

`[Photo 3: Electronics enclosure — Raspberry Pi, three microcontroller boards, wiring]`
*Electronics enclosure: Raspberry Pi 4, ESP8266, Arduino Mega, Arduino Uno, relay board.*

---

## 7. Project Links

**GitHub repository:** `[link]`

**Live demo / video:** `[link]`

---

---

## Appendix: Code-vs-Overview Discrepancies

The following discrepancies were found between `AutoSauce_Portfolio_Overview.md` (the narrative overview doc) and the actual source code. The portfolio above uses the verified code values.

| Claim in overview | Actual value in code | Source |
|---|---|---|
| GantryCode ~600 lines | 999 lines | `GantryCode.ino` (counted) |
| PrintheadCode ~500 lines | 540 lines | `PrintheadCode.ino` |
| ConveyorHotdogCode ~450 lines | 515 lines | `ConveyorHotdogCode.ino` |
| arduino_controller.py "150+" lines | 281 lines | `arduino_controller.py` |
| Sauce sweep start 1.65 in | 1.90 in | `GantryCode.ino` `SAUCE_START_INCHES` constant |
| Zigzag "±7.5 mm" (overview correct) but conveyor.py docstring says "±25 mm" | Firmware: ZIGZAG_DIST_MM = 15 (total width), so ±7.5 mm | `ConveyorHotdogCode.ino` line 20; `conveyor.py` docstring is wrong |
| "Active cascaded PID controller" implied | Cascaded PID code exists but is commented out; bang-bang is active | `GantryCode.ino` lines 402–440 commented out |
| Gear-lock applies "double-power burst" | Burst PWM = 1200 µs (300 units from neutral vs. normal 150 units — 2× the drive offset, not 2× the absolute speed) | `PrintheadCode.ino` line 267 |
