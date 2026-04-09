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
const int SIGNAL_PIN_GRABBER = 2;  // Pin for sending signals to grabber ESC

const int extruderA = 20;  // INT3
const int extruderB = 21;  // INT2
const int SIGNAL_PIN_EXTRUDER = 3; // Pin for sending signals to extr

//
// ====== ENCODER COUNTS ======
//
volatile long grabberTicks = 0;
volatile long extruderTicks = 0;

//
// ====== CONSTANTS ======
//
const long TICKS_PER_REV = 753;   // 1× decoding

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
// Simple, direct, one‑motor motion
//
void moveMotorTo(Servo &esc, volatile long &ticks, long target) {

  while (ticks != target) {

    if (ticks > target) {
      esc.writeMicroseconds(1350);   // move negative direction
    } 
    else if (ticks < target) {
      esc.writeMicroseconds(1650);   // move positive direction
    }
  }

  esc.writeMicroseconds(1500);  // stop
  delay(150);
}

//
// ====== SETUP ======
//
void setup() {
  Serial.begin(115200);

  // Encoder pins
  pinMode(grabberA, INPUT_PULLUP);
  pinMode(grabberB, INPUT_PULLUP);
  pinMode(extruderA, INPUT_PULLUP);
  pinMode(extruderB, INPUT_PULLUP);

  // Attach interrupts
  attachInterrupt(digitalPinToInterrupt(grabberA), ISR_grabberA, CHANGE);
  attachInterrupt(digitalPinToInterrupt(grabberB), ISR_grabberB, CHANGE);
  attachInterrupt(digitalPinToInterrupt(extruderA), ISR_extruderA, CHANGE);
  attachInterrupt(digitalPinToInterrupt(extruderB), ISR_extruderB, CHANGE);

  // ESCs
  escGrabber.attach(SIGNAL_PIN_GRABBER);  // Attach to any pin, we will use writeMicroseconds() with custom signals
  escExtruder.attach(SIGNAL_PIN_EXTRUDER);

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
    // 1700 is strong PWM for grabber
    homeMotor(escGrabber, grabberTicks, 1700);
    Serial.println("DONE");
  } 
  else if (cmd == "HOME_EXTRUDER") {
    // 1300 might be appropriate for extruder based on Python, but setup originally used 1700.
    // Changing to 1700 matching previous setup() homing, feel free to tune.
    homeMotor(escExtruder, extruderTicks, 1700);
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
