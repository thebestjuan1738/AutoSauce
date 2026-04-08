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

const int extruderA = 20;  // INT3
const int extruderB = 21;  // INT2

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
  escGrabber.attach(2);
  escExtruder.attach(3);

  escGrabber.writeMicroseconds(1500);
  escExtruder.writeMicroseconds(1500);
  delay(2000);

  Serial.println("=== HOMING BOTH MOTORS ===");

  // Home grabber
  Serial.println("Homing grabber...");
  homeMotor(escGrabber, grabberTicks, 1700);

  // Home extruder
  Serial.println("Homing extruder...");
  homeMotor(escExtruder, extruderTicks, 1700);

  Serial.println("=== HOMING COMPLETE ===");

  //
  // 1. Close gripper 1.8 revolutions
  //
  long gripperTarget = -(long)(2 * TICKS_PER_REV);
  Serial.print("Closing gripper to: ");
  Serial.println(gripperTarget);
  moveMotorTo(escGrabber, grabberTicks, gripperTarget);

  //
  // 2. Wait 3 seconds
  //
  Serial.println("Waiting 3 seconds...");
  delay(3000);

  //
  // 3. Close extruder 10 revolutions
  //
  long extruderTarget = -(long)(10.0 * TICKS_PER_REV);
  Serial.print("Closing extruder to: ");
  Serial.println(extruderTarget);
  moveMotorTo(escExtruder, extruderTicks, extruderTarget);

  //
  // 4. Stop forever
  //
  Serial.println("Sequence complete. Motors stopped.");
}

//
// ====== LOOP ======
//
void loop() {
  // Do nothing — sequence is complete
  escGrabber.writeMicroseconds(1500);
  escExtruder.writeMicroseconds(1500);
}
