#include <SPI.h>
#include <Servo.h>

// ---- Pin Definitions ----
const int CS_PIN      = 10;
const int CW_RELAY    = 6;
const int CCW_RELAY   = 7;
const int CONV_ENC_A  = 2;
const int CONV_ENC_B  = 8;
const int LAMP_RELAY  = 5;
const int SPARK_PIN   = 4;
const int CYL_ENC     = 3;
const int LIMIT_SW    = 9;

// ---- Station Positions (mm from home) ----
const long POS_HOTDOG = 255;
const long POS_HEAT   = 425;
const long POS_SAUCE  = 739;
const long POS_PICKUP = 1020;

// ---- Zigzag Configuration ----
const int ZIGZAG_DIST_MM = 25;

// ---- Conveyor ----
volatile long absEncoder  = 0;
long convTargetAbs        = 0;
bool convMoving           = false;
int convDirection         = 0;
bool convHomed            = false;

// ---- Move tracking ----
enum MoveType { MOVE_NONE, MOVE_HOTDOG, MOVE_HEAT, MOVE_SAUCE, MOVE_PICKUP, MOVE_FWD, MOVE_REV, MOVE_ZIGZAG };
MoveType currentMove = MOVE_NONE;

// ---- Cylinder tracking ----
enum CylMove { CYL_NONE, CYL_GRAB, CYL_DROP, CYL_POS };
CylMove currentCylMove = CYL_NONE;

// ---- Zigzag State ----
bool zigzagActive      = false;
bool zigzagInitialPass = false;
bool zigzagStopping    = false;
int zigzagDirection    = 1;
long zigzagCenterAbs   = 0;

// ---- Cylinder ----
const float KP          = 1.5;
const float KI          = 0.01;
const float KD          = 0.02;
const int   PWM_NEUTRAL = 1500;
const int   PWM_MAX_FWD = 2500;
const int   PWM_MAX_REV = 500;
const float DEAD_BAND   = 2.0;
const int   MIN_OUTPUT  = 10;
const int   MAX_OUTPUT  = 30;
const float GRAB_DEG    = 274.0;
const float DROP_DEG    = 197.0;

Servo spark;
float cylTarget  = 0;
bool cylRunning  = false;

volatile unsigned long riseTime   = 0;
volatile unsigned long pulseWidth = 0;

// ---- Command Buffer ----
char cmdBuf[24];
byte cmdIdx = 0;

// ---- Encoder ISRs ----
void convEncoderISR() {
  bool b = digitalRead(CONV_ENC_B);
  if (b == LOW) absEncoder++;
  else absEncoder--;
}

void cylEncoderISR() {
  if (digitalRead(CYL_ENC) == HIGH) {
    riseTime = micros();
  } else {
    pulseWidth = micros() - riseTime;
  }
}

// ---- Digipot ----
void setWiper(byte val) {
  digitalWrite(CS_PIN, LOW);
  SPI.transfer(0x11);
  SPI.transfer(val);
  digitalWrite(CS_PIN, HIGH);
}

// ---- Conveyor ----
void convStop() {
  setWiper(0);
  delay(10);
  digitalWrite(CW_RELAY, HIGH);
  digitalWrite(CCW_RELAY, HIGH);
  convMoving    = false;
  convDirection = 0;
}

void convStartMove(long targetAbs, int dir) {
  convTargetAbs = targetAbs;
  convMoving    = true;
  convDirection = dir;
  setWiper(255);

  if (dir == 1) {
    digitalWrite(CCW_RELAY, HIGH);
    delay(5);
    digitalWrite(CW_RELAY, LOW);
  } else {
    digitalWrite(CW_RELAY, HIGH);
    delay(5);
    digitalWrite(CCW_RELAY, LOW);
  }
}

void goToStation(long stationMM, const __FlashStringHelper* name, MoveType moveType) {
  if (!convHomed) {
    Serial.println(F("[CONV] Not homed. Send HOME first."));
    return;
  }
  if (convMoving || zigzagActive) {
    Serial.println(F("[CONV] Already moving."));
    return;
  }
  if (stationMM <= absEncoder) {
    Serial.println(F("[CONV] Station behind current position."));
    return;
  }
  currentMove = moveType;
  Serial.print(F("[CONV] Moving to "));
  Serial.print(name);
  Serial.print(F(" target: "));
  Serial.print(stationMM);
  Serial.println(F(" mm"));
  convStartMove(stationMM, 1);
}

void runConveyor() {
  long currentAbs = absEncoder;

  if (convDirection == 1 && currentAbs >= convTargetAbs) {
    convStop();
    delay(10);
    onConveyorDone();
  } else if (convDirection == -1 && currentAbs <= convTargetAbs) {
    convStop();
    delay(10);
    onConveyorDone();
  }
}

void onConveyorDone() {
  if (zigzagActive) {
    if (zigzagInitialPass) {
      zigzagInitialPass = false;

      if (zigzagStopping) {
        zigzagActive   = false;
        zigzagStopping = false;
        currentMove    = MOVE_NONE;
        Serial.println(F("[ZIGZAG] Stopped. Returning to center."));
        convStartMove(zigzagCenterAbs, 1);
        Serial.println(F("MOVE_DONE:ZIGZAG"));
        return;
      }

      zigzagDirection = 1;
      convStartMove(zigzagCenterAbs + (ZIGZAG_DIST_MM / 2), zigzagDirection);
      return;
    }

    if (zigzagStopping) {
      zigzagActive   = false;
      zigzagStopping = false;
      currentMove    = MOVE_NONE;
      Serial.println(F("[ZIGZAG] Stopped. Returning to center."));
      convStartMove(zigzagCenterAbs, zigzagCenterAbs > absEncoder ? 1 : -1);
      Serial.println(F("MOVE_DONE:ZIGZAG"));
      return;
    }

    zigzagDirection *= -1;
    long nextTarget = (zigzagDirection == 1)
      ? zigzagCenterAbs + (ZIGZAG_DIST_MM / 2)
      : zigzagCenterAbs - (ZIGZAG_DIST_MM / 2);
    convStartMove(nextTarget, zigzagDirection);

  } else {
    Serial.print(F("[CONV] Done. Pos: "));
    Serial.print(absEncoder);
    Serial.println(F(" mm"));

    switch (currentMove) {
      case MOVE_HOTDOG: Serial.println(F("MOVE_DONE:HOTDOG")); break;
      case MOVE_HEAT:   Serial.println(F("MOVE_DONE:HEAT"));   break;
      case MOVE_SAUCE:  Serial.println(F("MOVE_DONE:SAUCE"));  break;
      case MOVE_PICKUP: Serial.println(F("MOVE_DONE:PICKUP")); break;
      case MOVE_FWD:    Serial.println(F("MOVE_DONE:FWD"));    break;
      case MOVE_REV:    Serial.println(F("MOVE_DONE:REV"));    break;
      default: break;
    }
    currentMove = MOVE_NONE;
  }
}

// ---- Limit Switch ----
void checkLimitSwitch() {
  if (digitalRead(LIMIT_SW) == LOW) {
    absEncoder      = 0;
    convHomed       = true;
    convStop();
    zigzagActive    = false;
    zigzagStopping  = false;
    currentMove     = MOVE_NONE;
    Serial.println(F("[HOME] Limit switch triggered. Position reset to 0."));
    Serial.println(F("HOME_DONE"));
  }
}

// ---- Cylinder ----
float angleDiff(float target, float current) {
  float diff = target - current;
  if (diff >  180) diff -= 360;
  if (diff < -180) diff += 360;
  return diff;
}

float readCylPosition(bool filterEnabled) {
  static float lastValid = -1;
  noInterrupts();
  unsigned long pw = pulseWidth;
  interrupts();
  if (pw == 0) return -1;
  float pos = constrain(map(pw, 1, 1024, 0, 3600) / 10.0, 0, 360);
  if (filterEnabled && lastValid >= 0 && abs(angleDiff(pos, lastValid)) > 40) {
    return lastValid;
  }
  lastValid = pos;
  return pos;
}

void setCylMotor(int speed) {
  speed   = constrain(speed, -MAX_OUTPUT, MAX_OUTPUT);
  int pwm = map(speed, -100, 100, PWM_MAX_FWD, PWM_MAX_REV);
  spark.writeMicroseconds(pwm);
}

void stopCylMotor() {
  spark.writeMicroseconds(PWM_NEUTRAL);
}

void runCylinder() {
  static float integral         = 0;
  static float lastError        = 0;
  static unsigned long lastTime = 0;

  float currentPos = readCylPosition(true);
  if (currentPos < 0) {
    stopCylMotor();
    return;
  }

  float error    = angleDiff(cylTarget, currentPos);
  float absError = abs(error);

  if (absError <= DEAD_BAND) {
    stopCylMotor();
    cylRunning = false;
    integral   = 0;
    lastError  = 0;
    if (!convMoving) {
      Serial.print(F("[CYL] Done: "));
      Serial.print(currentPos, 1);
      Serial.println(F(" deg"));
      switch (currentCylMove) {
        case CYL_GRAB: Serial.println(F("CYL_DONE:GRAB")); break;
        case CYL_DROP: Serial.println(F("CYL_DONE:DROP")); break;
        case CYL_POS:  Serial.println(F("CYL_DONE:POS"));  break;
        default: break;
      }
      currentCylMove = CYL_NONE;
    }
    return;
  }

  unsigned long now     = millis();
  unsigned long elapsed = now - lastTime;
  if (elapsed == 0) return;

  float dt = elapsed / 1000.0;
  lastTime = now;

  if (absError < 10.0) {
    integral += error * dt;
    integral  = constrain(integral, -20, 20);
  } else {
    integral = 0;
  }

  float derivative = (error - lastError) / dt;
  lastError        = error;
  float output     = KP * error + KI * integral + KD * derivative;

  if (output > 0 && output <  MIN_OUTPUT) output =  MIN_OUTPUT;
  if (output < 0 && output > -MIN_OUTPUT) output = -MIN_OUTPUT;
  output = constrain(output, -MAX_OUTPUT, MAX_OUTPUT);

  setCylMotor(-(int)output);

  static unsigned long lastCylPrint = 0;
  if (!convMoving && millis() - lastCylPrint >= 100) {
    Serial.print(F("[CYL] "));
    Serial.print(currentPos, 1);
    Serial.print(F(" deg  Err: "));
    Serial.print(error, 1);
    Serial.print(F("  Out: "));
    Serial.println((int)output);
    lastCylPrint = millis();
  }
}

// ---- Serial ----
void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdIdx > 0) {
        cmdBuf[cmdIdx] = '\0';
        handleCommand();
        cmdIdx = 0;
      }
    } else if (cmdIdx < 23) {
      cmdBuf[cmdIdx++] = toupper(c);
    }
  }
}

void handleCommand() {
  if (strcmp(cmdBuf, "HOME") == 0) {
    absEncoder      = 0;
    convHomed       = true;
    zigzagActive    = false;
    zigzagStopping  = false;
    currentMove     = MOVE_NONE;
    Serial.println(F("[HOME] Position manually zeroed."));
    Serial.println(F("HOME_DONE"));

  } else if (strcmp(cmdBuf, "HOTDOG") == 0) {
    goToStation(POS_HOTDOG, F("HOTDOG"), MOVE_HOTDOG);

  } else if (strcmp(cmdBuf, "HEAT") == 0) {
    goToStation(POS_HEAT, F("HEAT"), MOVE_HEAT);

  } else if (strcmp(cmdBuf, "SAUCE") == 0) {
    goToStation(POS_SAUCE, F("SAUCE"), MOVE_SAUCE);

  } else if (strcmp(cmdBuf, "PICKUP") == 0) {
    goToStation(POS_PICKUP, F("PICKUP"), MOVE_PICKUP);

  } else if (strncmp(cmdBuf, "FWD", 3) == 0) {
    if (!convMoving && !zigzagActive) {
      int dist = atoi(cmdBuf + 3);
      if (dist > 0) {
        currentMove = MOVE_FWD;
        convStartMove(absEncoder + dist, 1);
        Serial.print(F("[CONV] Fwd "));
        Serial.print(dist);
        Serial.println(F(" mm"));
      } else Serial.println(F("Example: FWD178"));
    } else Serial.println(F("[CONV] Busy."));

  } else if (strncmp(cmdBuf, "REV", 3) == 0) {
    if (!convMoving && !zigzagActive) {
      int dist = atoi(cmdBuf + 3);
      if (dist > 0) {
        currentMove = MOVE_REV;
        convStartMove(absEncoder - dist, -1);
        Serial.print(F("[CONV] Rev "));
        Serial.print(dist);
        Serial.println(F(" mm"));
      } else Serial.println(F("Example: REV178"));
    } else Serial.println(F("[CONV] Busy."));

  } else if (strcmp(cmdBuf, "ZIGZAG") == 0) {
    if (!convMoving && !zigzagActive) {
      zigzagActive      = true;
      zigzagInitialPass = true;
      zigzagStopping    = false;
      zigzagDirection   = -1;
      zigzagCenterAbs   = absEncoder;
      currentMove       = MOVE_ZIGZAG;
      Serial.print(F("[ZIGZAG] Started. Width: "));
      Serial.print(ZIGZAG_DIST_MM);
      Serial.println(F(" mm. Send ZIGZAGSTOP to stop."));
      convStartMove(absEncoder - (ZIGZAG_DIST_MM / 2), -1);
    } else Serial.println(F("[ZIGZAG] Busy."));

  } else if (strcmp(cmdBuf, "ZIGZAGSTOP") == 0) {
    if (zigzagActive) {
      zigzagStopping = true;
      Serial.println(F("[ZIGZAG] Stop requested. Will stop after current pass."));
    } else {
      Serial.println(F("[ZIGZAG] Not running."));
    }

  } else if (strcmp(cmdBuf, "CONVSTOP") == 0) {
    convStop();
    zigzagActive      = false;
    zigzagInitialPass = false;
    zigzagStopping    = false;
    currentMove       = MOVE_NONE;
    Serial.println(F("[CONV] Stopped."));
    Serial.println(F("CONV_STOPPED"));

  } else if (strcmp(cmdBuf, "GRAB") == 0) {
    cylTarget      = GRAB_DEG;
    cylRunning     = true;
    currentCylMove = CYL_GRAB;
    Serial.println(F("[CYL] GRAB"));

  } else if (strcmp(cmdBuf, "DROP") == 0) {
    cylTarget      = DROP_DEG;
    cylRunning     = true;
    currentCylMove = CYL_DROP;
    Serial.println(F("[CYL] DROP"));

  } else if (strncmp(cmdBuf, "POS", 3) == 0) {
    float target = atof(cmdBuf + 3);
    if (target >= 0 && target <= 360) {
      cylTarget      = target;
      cylRunning     = true;
      currentCylMove = CYL_POS;
      Serial.print(F("[CYL] "));
      Serial.print(target, 1);
      Serial.println(F(" deg"));
    } else Serial.println(F("Invalid. 0-360."));

  } else if (strcmp(cmdBuf, "CYLSTOP") == 0) {
    cylRunning     = false;
    currentCylMove = CYL_NONE;
    stopCylMotor();
    Serial.println(F("[CYL] Stopped."));
    Serial.println(F("CYL_STOPPED"));

  } else if (strcmp(cmdBuf, "LAMPON") == 0) {
    digitalWrite(LAMP_RELAY, HIGH);
    Serial.println(F("[LAMP] ON."));
    Serial.println(F("LAMP_DONE:ON"));

  } else if (strcmp(cmdBuf, "LAMPOFF") == 0) {
    digitalWrite(LAMP_RELAY, LOW);
    Serial.println(F("[LAMP] OFF."));
    Serial.println(F("LAMP_DONE:OFF"));

  } else if (strcmp(cmdBuf, "STATUS") == 0) {
    Serial.println(F("---- STATUS ----"));
    Serial.print(F("[CONV] Pos: "));
    Serial.print(absEncoder);
    Serial.print(F(" mm  Homed:"));
    Serial.print(convHomed ? F("Y") : F("N"));
    Serial.print(F(" Moving:"));
    Serial.print(convMoving ? F("Y") : F("N"));
    Serial.print(F(" Zigzag:"));
    Serial.println(zigzagActive ? F("Y") : F("N"));
    Serial.print(F("[CYL]  "));
    Serial.print(readCylPosition(false), 1);
    Serial.print(F(" deg  Moving:"));
    Serial.println(cylRunning ? F("Y") : F("N"));
    Serial.print(F("[LAMP] "));
    Serial.println(digitalRead(LAMP_RELAY) ? F("ON") : F("OFF"));
    Serial.println(F("----------------"));

  } else {
    Serial.println(F("Commands: HOME HOTDOG HEAT SAUCE PICKUP"));
    Serial.println(F("FWD<mm> REV<mm> CONVSTOP"));
    Serial.println(F("ZIGZAG ZIGZAGSTOP"));
    Serial.println(F("GRAB DROP POS<deg> CYLSTOP"));
    Serial.println(F("LAMPON LAMPOFF STATUS"));
  }
}

// ---- Setup ----
void setup() {
  pinMode(CW_RELAY, OUTPUT);   digitalWrite(CW_RELAY, HIGH);
  pinMode(CCW_RELAY, OUTPUT);  digitalWrite(CCW_RELAY, HIGH);
  pinMode(LAMP_RELAY, OUTPUT); digitalWrite(LAMP_RELAY, LOW);
  pinMode(CS_PIN, OUTPUT);     digitalWrite(CS_PIN, HIGH);
  pinMode(LIMIT_SW, INPUT_PULLUP);
  pinMode(CONV_ENC_A, INPUT);
  pinMode(CONV_ENC_B, INPUT);
  attachInterrupt(digitalPinToInterrupt(CONV_ENC_A), convEncoderISR, RISING);
  pinMode(CYL_ENC, INPUT);
  attachInterrupt(digitalPinToInterrupt(CYL_ENC), cylEncoderISR, CHANGE);

  spark.attach(SPARK_PIN, 500, 2500);
  stopCylMotor();
  SPI.begin();
  setWiper(0);
  Serial.begin(9600);
  delay(500);

  float cylPos = readCylPosition(false);
  Serial.print(F("[CYL] Start: "));
  Serial.print(cylPos, 1);
  Serial.println(F(" deg"));
  Serial.println(F("Send HOME to zero position."));
  Serial.println(F("Type STATUS for system state."));
}

// ---- Loop ----
void loop() {
  checkLimitSwitch();
  readSerial();
  if (convMoving) runConveyor();
  if (cylRunning) runCylinder();
}