#include <Servo.h>

//
// ====== MOTOR OBJECTS ======
//
Servo escGrabber;
Servo escExtruder;

//
// ====== ENCODER PINS ======
//
const int grabberA = 18;   // INT5
const int grabberB = 19;   // INT4
const int SIGNAL_PIN_GRABBER = 2;

const int extruderA = 20;  // INT3
const int extruderB = 21;  // INT2
const int SIGNAL_PIN_EXTRUDER = 3;

//
// ====== PLUNGER SENSOR PIN (INPUT_PULLUP — goes LOW on contact) ======
//
const int plungerPin = 52;

//
// ====== ENCODER COUNTS ======
//
volatile long grabberTicks  = 0;
volatile long extruderTicks = 0;

//
// ====== CONSTANTS ======
//
const long TICKS_PER_REV      = 753;
const long TICKS_PAST_CONTACT = 376;  // ticks to advance past plunger contact (~0.5 rev); tune as needed

//
// ====== QUADRATURE ISR: GRABBER ======
//
void ISR_grabberA() {
  bool A = digitalRead(grabberA);
  bool B = digitalRead(grabberB);
  if (A == B) grabberTicks++;
  else        grabberTicks--;
}

void ISR_grabberB() {
  bool A = digitalRead(grabberA);
  bool B = digitalRead(grabberB);
  if (A != B) grabberTicks++;
  else        grabberTicks--;
}

//
// ====== QUADRATURE ISR: EXTRUDER ======
//
void ISR_extruderA() {
  bool A = digitalRead(extruderA);
  bool B = digitalRead(extruderB);
  if (A == B) extruderTicks++;
  else        extruderTicks--;
}

void ISR_extruderB() {
  bool A = digitalRead(extruderA);
  bool B = digitalRead(extruderB);
  if (A != B) extruderTicks++;
  else        extruderTicks--;
}

//
// ====== FULL‑POWER HOMING FUNCTION ======
// Push hard until stall → zero encoder
//
long homeMotor(Servo &esc, volatile long &ticks, int strongPWM) {

  Serial.println("Homing with FULL POWER...");

  long lastTicks = ticks;
  unsigned long lastMoveTime = millis();

  while (true) {
    esc.writeMicroseconds(strongPWM);   // FULL POWER ALWAYS

    if (ticks != lastTicks) {
      lastTicks = ticks;
      lastMoveTime = millis();
    }

    // Stall = no movement for 200 ms
    if (millis() - lastMoveTime > 200) {
      esc.writeMicroseconds(1500);  // stop
      delay(200);
      ticks = 0;                    // zero encoder
      Serial.println("Homing complete. Encoder zeroed.");
      return 0;
    }
  }
}

//
// ====== BLOCKING MOVE TO TARGET ======
// Drives toward target, stops when within MOVE_TOLERANCE ticks.
// Startup timeout: waits up to MOVE_START_MS for the first encoder tick.
// Stall timeout: aborts if no tick arrives within MOVE_STALL_MS mid-move.
//
const long MOVE_TOLERANCE = 8;     // ±8 ticks (~±1° at 753 ticks/rev)
const long MOVE_START_MS  = 4000;  // allow up to 4 s for motor to start moving
const long MOVE_STALL_MS  = 1000;  // abort if stalled mid-move for 1 s

void moveMotorTo(Servo &esc, volatile long &ticks, long target) {

  long lastTicks             = ticks;
  unsigned long startTime    = millis();
  unsigned long lastMoveTime = 0;   // 0 = not yet moving
  bool started               = false;

  while (abs(ticks - target) > MOVE_TOLERANCE) {

    if (ticks > target) {
      esc.writeMicroseconds(1300);   // move negative direction
    } else {
      esc.writeMicroseconds(1700);   // move positive direction
    }

    if (ticks != lastTicks) {
      lastTicks    = ticks;
      lastMoveTime = millis();
      started      = true;
    }

    if (!started) {
      // Still waiting for first tick — apply startup timeout
      if (millis() - startTime > MOVE_START_MS) {
        Serial.println("WARN: moveMotorTo motor did not start — aborting");
        break;
      }
    } else {
      // Moving — apply stall timeout between ticks
      if (millis() - lastMoveTime > MOVE_STALL_MS) {
        Serial.println("WARN: moveMotorTo stall detected — aborting");
        break;
      }
    }
  }

  esc.writeMicroseconds(1500);  // stop
  delay(150);
}

//
// ====== USER FUNCTIONS ======
//
void home_grabber() {
  Serial.println("Homing grabber...");
  homeMotor(escGrabber, grabberTicks, 1700);
}

void home_extruder() {
  Serial.println("Homing extruder...");
  homeMotor(escExtruder, extruderTicks, 1700);
}

void close_grabber() {
  long target = -(long)(1.8 * TICKS_PER_REV);
  Serial.print("Closing gripper to: ");
  Serial.println(target);
  moveMotorTo(escGrabber, grabberTicks, target);
}

void open_grabber() {
  Serial.println("Opening gripper FAST...");
  while (grabberTicks < 0) {
    escGrabber.writeMicroseconds(1650);
  }
  escGrabber.writeMicroseconds(1500);
  delay(100);
}

void open_extruder() {
  Serial.println("Opening extruder FAST...");
  while (extruderTicks < 0) {
    escExtruder.writeMicroseconds(1650);
  }
  escExtruder.writeMicroseconds(1500);
  delay(100);
}

//
// ====== MEET PLUNGER (collision detection + measured advance) ======
// Phase 1: Drive extruder until plungerPin goes LOW (contact detected).
// Phase 2: Advance TICKS_PAST_CONTACT more ticks past the contact point.
//
void meet_plunger() {
  Serial.println("Phase 1: moving until plunger contact...");

  long lastTicks             = extruderTicks;
  unsigned long lastMoveTime = millis();

  while (digitalRead(plungerPin) == HIGH) {
    escExtruder.writeMicroseconds(1350);  // closing/extending direction
    if (extruderTicks != lastTicks) {
      lastTicks    = extruderTicks;
      lastMoveTime = millis();
    }
    if (millis() - lastMoveTime > 400) {
      escExtruder.writeMicroseconds(1500);
      Serial.println("WARN: meet_plunger stalled — no contact detected. Aborting.");
      return;
    }
  }
  escExtruder.writeMicroseconds(1500);
  delay(200);
  Serial.println("Plunger contact detected.");

  long targetTicks = extruderTicks - TICKS_PAST_CONTACT;
  Serial.print("Phase 2: advancing ");
  Serial.print(TICKS_PAST_CONTACT);
  Serial.print(" ticks past contact to ");
  Serial.println(targetTicks);
  moveMotorTo(escExtruder, extruderTicks, targetTicks);
  Serial.println("meet_plunger complete.");
}

//
// ====== SETUP ======
//
void setup() {
  Serial.begin(115200);

  // Encoder pins
  pinMode(grabberA,  INPUT_PULLUP);
  pinMode(grabberB,  INPUT_PULLUP);
  pinMode(extruderA, INPUT_PULLUP);
  pinMode(extruderB, INPUT_PULLUP);

  // Plunger contact sensor
  pinMode(plungerPin, INPUT_PULLUP);

  // Attach encoder interrupts
  attachInterrupt(digitalPinToInterrupt(grabberA),  ISR_grabberA,  CHANGE);
  attachInterrupt(digitalPinToInterrupt(grabberB),  ISR_grabberB,  CHANGE);
  attachInterrupt(digitalPinToInterrupt(extruderA), ISR_extruderA, CHANGE);
  attachInterrupt(digitalPinToInterrupt(extruderB), ISR_extruderB, CHANGE);

  // Attach ESCs
  escGrabber.attach(SIGNAL_PIN_GRABBER);
  escExtruder.attach(SIGNAL_PIN_EXTRUDER);

  // ESC arming sequence: full-high → full-low → neutral
  escGrabber.writeMicroseconds(1900);
  escExtruder.writeMicroseconds(1900);
  delay(2000);

  escGrabber.writeMicroseconds(1100);
  escExtruder.writeMicroseconds(1100);
  delay(2000);

  escGrabber.writeMicroseconds(1500);
  escExtruder.writeMicroseconds(1500);
  delay(2000);

  Serial.println("=== ARDUINO READY ===");
}

//
// ====== COMMAND PARSER ======
//
void processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd == "HOME_GRIPPER") {
    home_grabber();
    Serial.println("DONE");
  }
  else if (cmd == "HOME_EXTRUDER") {
    home_extruder();
    Serial.println("DONE");
  }
  else if (cmd.startsWith("MOVE_GRIPPER:")) {
    long target = cmd.substring(13).toInt();
    moveMotorTo(escGrabber, grabberTicks, target);
    Serial.println("DONE");
  }
  else if (cmd.startsWith("MOVE_EXTRUDER:")) {
    long target = cmd.substring(14).toInt();
    moveMotorTo(escExtruder, extruderTicks, target);
    Serial.println("DONE");
  }
  else if (cmd == "MEET_PLUNGER") {
    meet_plunger();
    Serial.println("DONE");
  }
  else if (cmd == "CLOSE_GRABBER") {
    close_grabber();
    Serial.println("DONE");
  }
  else if (cmd == "OPEN_GRABBER") {
    open_grabber();
    Serial.println("DONE");
  }
  else if (cmd == "OPEN_EXTRUDER") {
    open_extruder();
    Serial.println("DONE");
  }
  else if (cmd == "PING") {
    Serial.println("PONG");
  }
  else {
    Serial.println("ERR: Unknown Command");
    Serial.println("DONE");
  }
}

//
// ====== LOOP ======
//
void loop() {
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    processCommand(cmd);
  }
}
