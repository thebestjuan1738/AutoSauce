#include <Servo.h>

//
// ====== MOTOR OBJECTS ======
//
Servo escGrabber;
Servo escExtruder;

//
// ====== ENCODER PINS ======
//
const int grabberA  = 18;  // INT5
const int grabberB  = 19;  // INT4
const int extruderA = 20;  // INT3
const int extruderB = 21;  // INT2

//
// ====== PLUNGER SENSOR PIN ======
//
const int plungerPin = 52;  // LOW when plunger is hit

//
// ====== ENCODER COUNTS ======
//
volatile long grabberTicks  = 0;
volatile long extruderTicks = 0;

//
// ====== CONSTANTS ======
// goBILDA 1x15A controller: 1050 = full reverse, 1500 = stop, 1950 = full forward
//
const long TICKS_PER_REV         = 753;
const int  GOBILDA_STOP          = 1500;
const int  GOBILDA_FULL_FWD      = 1950;
const int  GOBILDA_FULL_REV      = 1050;
const int  MEET_PLUNGER_SPEED    = 1350;  // change to 1650 if direction is wrong
const int  MEET_PLUNGER_DEBOUNCE = 5;
const int  EXTRUDE_SLOW          = 1430;
const int  EXTRUDE_MED           = 1400;
const int  EXTRUDE_FAST          = 1370;

//
// ====== STATE MACHINE ======
//
bool meetPlungerActive = false;
int  plungerLowCount   = 0;
bool extruding         = false;

//
// ====== SERIAL COMMAND BUFFER ======
//
String cmdBuffer = "";

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
// ====== FULL-POWER HOMING ======
//
long homeMotor(Servo &esc, volatile long &ticks, int strongPWM) {
  long lastTicks = ticks;
  unsigned long lastMoveTime = millis();

  while (true) {
    esc.writeMicroseconds(strongPWM);

    if (ticks != lastTicks) {
      lastTicks = ticks;
      lastMoveTime = millis();
    }

    if (millis() - lastMoveTime > 200) {
      esc.writeMicroseconds(GOBILDA_STOP);
      delay(200);
      ticks = 0;
      return 0;
    }
  }
}

//
// ====== BLOCKING MOVE TO TARGET ======
//
void moveMotorTo(Servo &esc, volatile long &ticks, long target) {
  while (ticks != target) {
    if      (ticks > target) esc.writeMicroseconds(1300);
    else if (ticks < target) esc.writeMicroseconds(1700);
  }
  esc.writeMicroseconds(GOBILDA_STOP);
  delay(150);
}

//
// ====== USER FUNCTIONS ======
//
void home_grabber() {
  Serial.println("Homing grabber...");
  homeMotor(escGrabber, grabberTicks, 1700);
  Serial.println("Grabber homed.");
}

void home_extruder() {
  Serial.println("Homing extruder...");
  homeMotor(escExtruder, extruderTicks, 1700);
  Serial.println("Extruder homed.");
}

void close_grabber() {
  long target = -(long)(1.0 * TICKS_PER_REV);
  Serial.print("Closing grabber to tick target: ");
  Serial.println(target);
  moveMotorTo(escGrabber, grabberTicks, target);
  Serial.println("Grabber closed.");
  Serial.println("GRAB_DONE");
}

void open_grabber() {
  Serial.println("Opening grabber...");
  while (grabberTicks < 0) {
    escGrabber.writeMicroseconds(1750);
  }
  escGrabber.writeMicroseconds(GOBILDA_STOP);
  delay(100);
  Serial.println("Grabber open.");
  Serial.println("RELEASE_DONE");
}

void open_extruder() {
  Serial.println("Opening extruder...");
  while (extruderTicks < 0) {
    escExtruder.writeMicroseconds(1850);
  }
  escExtruder.writeMicroseconds(GOBILDA_STOP);
  delay(100);
  Serial.println("Extruder open.");
  Serial.println("OPENEXT_DONE");
}

void start_meet_plunger() {
  Serial.println("Moving extruder to plunger contact...");
  Serial.print("Using PWM: ");
  Serial.println(MEET_PLUNGER_SPEED);
  Serial.println("Type STOP at any time to abort.");
  meetPlungerActive = true;
  plungerLowCount   = 0;
}

void stop_all() {
  escGrabber.writeMicroseconds(GOBILDA_STOP);
  escExtruder.writeMicroseconds(GOBILDA_STOP);
  meetPlungerActive = false;
  extruding         = false;
  plungerLowCount   = 0;
  Serial.println("All motors stopped.");
  Serial.println("STOPPED");
}

//
// ====== SERIAL COMMAND PARSER ======
//
void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "HOME") {
    stop_all();
    home_grabber();
    home_extruder();
    Serial.println("READY");

  } else if (cmd == "HOMEGRAB") {
    stop_all();
    home_grabber();
    Serial.println("HOMEGRAB_DONE");

  } else if (cmd == "HOMEEXT") {
    stop_all();
    home_extruder();
    Serial.println("HOMEEXT_DONE");

  } else if (cmd == "GRAB") {
    close_grabber();

  } else if (cmd == "RELEASE") {
    open_grabber();

  } else if (cmd == "MEETPLUNGER") {
    start_meet_plunger();

  } else if (cmd == "OPENEXT") {
    open_extruder();

  } else if (cmd == "STOP") {
    stop_all();

  } else if (cmd == "STOPEXT") {
    escExtruder.writeMicroseconds(GOBILDA_STOP);
    meetPlungerActive = false;
    extruding         = false;
    Serial.println("Extruder stopped.");
    Serial.println("STOPEXT_DONE");

  } else if (cmd == "EXTRUDESLOW") {
    Serial.println("Extruding SLOW (PWM 1430)");
    escExtruder.writeMicroseconds(EXTRUDE_SLOW);
    extruding = true;
    Serial.println("EXTRUDING");

  } else if (cmd == "EXTRUDEMED") {
    Serial.println("Extruding MED (PWM 1400)");
    escExtruder.writeMicroseconds(EXTRUDE_MED);
    extruding = true;
    Serial.println("EXTRUDING");

  } else if (cmd == "EXTRUDEFAST") {
    Serial.println("Extruding FAST (PWM 1370)");
    escExtruder.writeMicroseconds(EXTRUDE_FAST);
    extruding = true;
    Serial.println("EXTRUDING");

  } else if (cmd == "TESTEXT") {
    Serial.println("Nudging extruder for 500ms at PWM 1350...");
    escExtruder.writeMicroseconds(1350);
    delay(500);
    escExtruder.writeMicroseconds(GOBILDA_STOP);
    noInterrupts();
    long e = extruderTicks;
    interrupts();
    Serial.print("Extruder ticks after nudge: ");
    Serial.println(e);
    Serial.println("Positive = correct direction. Zero/negative = swap motor wires.");
    Serial.println("TESTEXT_DONE");

  } else if (cmd == "TESTGRAB") {
    Serial.println("Nudging grabber for 500ms at PWM 1350...");
    escGrabber.writeMicroseconds(1350);
    delay(500);
    escGrabber.writeMicroseconds(GOBILDA_STOP);
    noInterrupts();
    long g = grabberTicks;
    interrupts();
    Serial.print("Grabber ticks after nudge: ");
    Serial.println(g);
    Serial.println("TESTGRAB_DONE");

  } else if (cmd == "SENSORCHECK") {
    Serial.println("Reading plunger sensor for 5 seconds...");
    unsigned long start = millis();
    while (millis() - start < 5000) {
      Serial.println(digitalRead(plungerPin) == LOW ? "LOW (contact)" : "HIGH (no contact)");
      delay(200);
    }
    Serial.println("Sensor check done.");
    Serial.println("SENSORCHECK_DONE");

  } else if (cmd == "POS") {
    noInterrupts();
    long g = grabberTicks;
    long e = extruderTicks;
    interrupts();
    Serial.print("Grabber ticks:  "); Serial.println(g);
    Serial.print("Extruder ticks: "); Serial.println(e);
    Serial.println("POS_DONE");

  } else if (cmd == "ZERO") {
    noInterrupts();
    grabberTicks  = 0;
    extruderTicks = 0;
    interrupts();
    Serial.println("Both encoders zeroed.");
    Serial.println("ZERO_DONE");

  } else if (cmd == "ZEROGRAB") {
    noInterrupts();
    grabberTicks = 0;
    interrupts();
    Serial.println("Grabber encoder zeroed.");
    Serial.println("ZEROGRAB_DONE");

  } else if (cmd == "ZEROEXT") {
    noInterrupts();
    extruderTicks = 0;
    interrupts();
    Serial.println("Extruder encoder zeroed.");
    Serial.println("ZEROEXT_DONE");

  } else if (cmd == "HELP") {
    Serial.println("=== AVAILABLE COMMANDS ===");
    Serial.println("HOME          - Home both motors         -> READY");
    Serial.println("HOMEGRAB      - Home grabber only        -> HOMEGRAB_DONE");
    Serial.println("HOMEEXT       - Home extruder only       -> HOMEEXT_DONE");
    Serial.println("GRAB          - Close grabber (1 rev)    -> GRAB_DONE");
    Serial.println("RELEASE       - Open grabber to home     -> RELEASE_DONE");
    Serial.println("MEETPLUNGER   - Drive to plunger contact -> PLUNGER_DONE");
    Serial.println("OPENEXT       - Open extruder to home    -> OPENEXT_DONE");
    Serial.println("EXTRUDESLOW   - Extrude slow (PWM 1430)  -> EXTRUDING");
    Serial.println("EXTRUDEMED    - Extrude medium (PWM 1400)-> EXTRUDING");
    Serial.println("EXTRUDEFAST   - Extrude fast (PWM 1370)  -> EXTRUDING");
    Serial.println("STOPEXT       - Stop extruder only       -> STOPEXT_DONE");
    Serial.println("STOP          - Stop all motors          -> STOPPED");
    Serial.println("TESTEXT       - Nudge extruder 500ms     -> TESTEXT_DONE");
    Serial.println("TESTGRAB      - Nudge grabber 500ms      -> TESTGRAB_DONE");
    Serial.println("SENSORCHECK   - Read plunger sensor 5s   -> SENSORCHECK_DONE");
    Serial.println("POS           - Report encoder positions -> POS_DONE");
    Serial.println("ZERO          - Zero both encoders       -> ZERO_DONE");
    Serial.println("ZEROGRAB      - Zero grabber encoder     -> ZEROGRAB_DONE");
    Serial.println("ZEROEXT       - Zero extruder encoder    -> ZEROEXT_DONE");

  } else {
    Serial.print("Unknown command: ");
    Serial.println(cmd);
    Serial.println("ERROR");
  }
}

//
// ====== SETUP ======
//
void setup() {
  Serial.begin(115200);

  pinMode(grabberA,   INPUT_PULLUP);
  pinMode(grabberB,   INPUT_PULLUP);
  pinMode(extruderA,  INPUT_PULLUP);
  pinMode(extruderB,  INPUT_PULLUP);
  pinMode(plungerPin, INPUT_PULLUP);

  attachInterrupt(digitalPinToInterrupt(grabberA),  ISR_grabberA,  CHANGE);
  attachInterrupt(digitalPinToInterrupt(grabberB),  ISR_grabberB,  CHANGE);
  attachInterrupt(digitalPinToInterrupt(extruderA), ISR_extruderA, CHANGE);
  attachInterrupt(digitalPinToInterrupt(extruderB), ISR_extruderB, CHANGE);

  escGrabber.attach(2);
  escExtruder.attach(3);

  escGrabber.writeMicroseconds(GOBILDA_STOP);
  escExtruder.writeMicroseconds(GOBILDA_STOP);
  delay(2000);

  Serial.println("=== HOMING BOTH MOTORS ===");
  home_grabber();
  home_extruder();
  Serial.println("=== HOMING COMPLETE ===");
  Serial.println("READY");
  delay(500);
}

//
// ====== LOOP ======
//
void loop() {

  // --- NON-BLOCKING MEET PLUNGER STATE MACHINE ---
  if (meetPlungerActive) {
    escExtruder.writeMicroseconds(MEET_PLUNGER_SPEED);

    if (digitalRead(plungerPin) == LOW) {
      plungerLowCount++;
    } else {
      plungerLowCount = 0;
    }

    if (plungerLowCount >= MEET_PLUNGER_DEBOUNCE) {
      escExtruder.writeMicroseconds(GOBILDA_STOP);
      meetPlungerActive = false;
      plungerLowCount   = 0;
      Serial.println("Plunger contact confirmed. Extruder stopped.");
      noInterrupts();
      long e = extruderTicks;
      interrupts();
      Serial.print("Extruder ticks at contact: ");
      Serial.println(e);
      Serial.println("PLUNGER_DONE");
    }
  }

  // --- SERIAL COMMAND READER ---
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdBuffer.length() > 0) {
        handleCommand(cmdBuffer);
        cmdBuffer = "";
      }
    } else {
      cmdBuffer += c;
    }
  }
}