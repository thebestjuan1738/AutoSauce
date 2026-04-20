#include <Servo.h>

// ---- Pin Definitions ----
#define ESC_PIN     5    // D1
#define ENC_A       14   // D5
#define ENC_B       12   // D6
#define LIMIT_MIN   13   // D7

// ---- ESC ----
Servo esc;
#define ESC_MIN   1000
#define ESC_STOP  1500
#define ESC_MAX   2000

// ---- Travel limits ----
#define COUNTS_PER_INCH           2053.67
#define MAX_TRAVEL_INCHES_DEFAULT 14.5
float maxTravelInches = MAX_TRAVEL_INCHES_DEFAULT;
long  maxTravelCounts = (long)(MAX_TRAVEL_INCHES_DEFAULT * COUNTS_PER_INCH);

// ---- Dock position ----
#define DOCK_POSITION_INCHES 14.0

// ---- Sauce positions ----
#define SAUCE_START_INCHES  6.3
#define SAUCE_END_INCHES    1.65
float sauceSpeed = 1.0;

// ---- Cascaded PID ----
float pos_Kp = 5.0;
float pos_Ki = 0.05;
float pos_Kd = 0.0;
float pos_integral  = 0;
float pos_lastError = 0;

float spd_Kp = 3.0;
float spd_Ki = 2.0;
float spd_Kd = 0.0;
float spd_integral  = 0;
float spd_lastError = 0;

#define POS_LOOP_MS   20
#define SPD_LOOP_MS   5
unsigned long lastPosLoop = 0;
unsigned long lastSpdLoop = 0;

// ---- Speed limits ----
float maxMoveSpeed           = 3.0;
float desiredSpeedSetpoint   = 0.0;
#define MIN_PULSE_OFFSET     30
#define POS_DEADBAND         50
#define SPD_CLAMP_DEFAULT    80

// ---- Homing ----
#define HOME_PHASE1_POWER    5
#define HOME_FWD_POWER       5
#define HOME_FWD_TIME_MS     2000
#define HOME_REV_POWER       5
#define HOME_ZERO_TOLERANCE  0.05

// ---- Move context — what triggered the current move ----
#define MOVE_CONTEXT_NONE   0
#define MOVE_CONTEXT_GOTO   1
#define MOVE_CONTEXT_DOCK   2
#define MOVE_CONTEXT_SAUCE1 3   // phase 1 — moving to start position
#define MOVE_CONTEXT_SAUCE2 4   // phase 2 — dispensing move
int currentMoveContext = MOVE_CONTEXT_NONE;

// ---- Sauce state ----
bool sauceActive     = false;
bool saucePhase1Done = false;

// ---- Encoder ----
volatile long encoderCount = 0;
volatile byte lastEncoded  = 0;

// ---- Speed measurement ----
float currentSpeedInchesPerSec = 0.0;
unsigned long lastSpeedTime    = 0;
long lastSpeedCount            = 0;

// ---- State ----
int   currentPulse   = ESC_STOP;
bool  limitTriggered = false;
bool  forwardBlocked = false;
bool  movingToTarget = false;
bool  homingActive   = false;
long  targetCounts   = 0;
bool  logging        = false;
unsigned long moveStartTime  = 0;
unsigned long statusInterval = 100;

void IRAM_ATTR updateEncoder();
void stopMotor();
void resetPID();
void runHomingRoutine();
bool moveToInches(float inches);
bool waitForMotion(int timeoutMs);
void updateSpeed();
void startMove(float inches, float speed, int context);

// =============================================================

void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);
  pinMode(LIMIT_MIN, INPUT_PULLUP);
  pinMode(ENC_A, INPUT_PULLUP);
  pinMode(ENC_B, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(ENC_A), updateEncoder, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_B), updateEncoder, CHANGE);

  Serial.println("\n\n");
  Serial.println("==============================");
  Serial.println("  Gantry Controller          ");
  Serial.println("  Cascaded PID               ");
  Serial.println("  NodeMCU ESP8266            ");
  Serial.println("==============================");
  Serial.println("");

  // ---- Test 1: LED ----
  Serial.println("[TEST 1] Onboard LED");
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_BUILTIN, LOW);  delay(300);
    digitalWrite(LED_BUILTIN, HIGH); delay(300);
  }
  Serial.println("  LED OK");
  Serial.println("");

  // ---- Test 2: Limit switch ----
  Serial.println("[TEST 2] Limit switch (D7)");
  Serial.print("  Current state: ");
  Serial.println(digitalRead(LIMIT_MIN) == LOW ? "TRIGGERED (check wiring)" : "CLEAR - OK");
  Serial.println("");

  // ---- Test 3: Encoder ----
  Serial.println("[TEST 3] Encoder (D5, D6)");
  Serial.println("  Rotate encoder shaft by hand...");
  Serial.println("  Watching for 5 seconds...");
  long startCount = encoderCount;
  unsigned long t = millis();
  while (millis() - t < 5000) {
    if (encoderCount != startCount) {
      Serial.print("  Encoder moving! Count: ");
      Serial.println(encoderCount);
      startCount = encoderCount;
    }
    delay(100);
  }
  Serial.println(encoderCount == 0 ? "  WARNING: No movement detected." : "  Encoder OK");
  Serial.println("");

  // ---- Test 4: ESC arming ----
  Serial.println("[TEST 4] ESC arming (D1)");
  esc.attach(ESC_PIN, ESC_MIN, ESC_MAX);
  esc.writeMicroseconds(ESC_STOP);
  Serial.println("  Waiting 3 seconds...");
  for (int i = 0; i < 6; i++) {
    digitalWrite(LED_BUILTIN, LOW);  delay(250);
    digitalWrite(LED_BUILTIN, HIGH); delay(250);
  }
  Serial.println("  ESC armed.");
  Serial.println("");

  lastSpeedTime  = millis();
  lastSpeedCount = encoderCount;

  Serial.println("==============================");
  Serial.println("  Boot tests complete.       ");
  Serial.println("==============================");
  Serial.println("");
  Serial.println("  Commands:");
  Serial.println("  GOTO<in>      Go to position     e.g. GOTO6.5");
  Serial.println("  DOCK          Go to dock position (14.0 in)");
  Serial.println("  SAUCE<in/s>   Run sauce sequence  e.g. SAUCE1.0");
  Serial.println("  FWD<0-100>    Forward power      e.g. FWD25");
  Serial.println("  REV<0-100>    Reverse power      e.g. REV25");
  Serial.println("  MSPD<in/s>    Set max move speed e.g. MSPD3.0");
  Serial.println("  SLIM<in>      Set soft limit     e.g. SLIM14.5");
  Serial.println("  PKP<val>      Set position Kp    e.g. PKP5.0");
  Serial.println("  PKI<val>      Set position Ki    e.g. PKI0.05");
  Serial.println("  PKD<val>      Set position Kd    e.g. PKD0.0");
  Serial.println("  SKP<val>      Set speed Kp       e.g. SKP3.0");
  Serial.println("  SKI<val>      Set speed Ki       e.g. SKI2.0");
  Serial.println("  SKD<val>      Set speed Kd       e.g. SKD0.0");
  Serial.println("  CLAMP<val>    Set speed clamp    e.g. CLAMP80");
  Serial.println("  SINT<ms>      Status interval    e.g. SINT100");
  Serial.println("  LOG           Toggle live data stream");
  Serial.println("  STOP / S      Stop motor");
  Serial.println("  ZERO          Run homing routine");
  Serial.println("  POS  / P      Print position");
  Serial.println("  DIAG / D      Run diagnostics");
  Serial.println("  HELP / H      Show commands");
  Serial.println("------------------------------");
  Serial.print("  Soft limit    : "); Serial.print(maxTravelInches, 1); Serial.println(" in");
  Serial.print("  Dock position : "); Serial.print(DOCK_POSITION_INCHES, 1); Serial.println(" in");
  Serial.print("  Sauce start   : "); Serial.print(SAUCE_START_INCHES, 2); Serial.println(" in");
  Serial.print("  Sauce end     : "); Serial.print(SAUCE_END_INCHES, 2); Serial.println(" in");
  Serial.print("  Sauce speed   : "); Serial.print(sauceSpeed, 2); Serial.println(" in/s");
  Serial.print("  Max move speed: "); Serial.print(maxMoveSpeed, 2); Serial.println(" in/s");
  Serial.print("  Counts/inch   : "); Serial.println(COUNTS_PER_INCH);
  Serial.println("------------------------------");

  // ---- Signal RPi that boot is complete and system is ready ----
  Serial.println("READY");
}

// =============================================================

unsigned long lastPrint = 0;
int spdClamp = SPD_CLAMP_DEFAULT;

void loop() {

  unsigned long now = millis();

  // ---- Update speed ----
  updateSpeed();


  // ---- Home limit switch ----
  if (!homingActive) {
    bool currentLimitState = digitalRead(LIMIT_MIN) == LOW;

    if (currentLimitState && !limitTriggered) {
      limitTriggered = true;
      encoderCount   = 0;
      stopMotor();
      movingToTarget     = false;
      logging            = false;
      currentMoveContext = MOVE_CONTEXT_NONE;
      if (sauceActive) {
        sauceActive     = false;
        saucePhase1Done = false;
        Serial.println("STOP DISPENSING");
      }
      digitalWrite(LED_BUILTIN, LOW);
      Serial.println("LIMIT TRIGGERED");
      Serial.println("[LIMIT] Home switch triggered. Position zeroed.");
      Serial.println("[LIMIT] Reverse blocked. Forward allowed.");

    } else if (!currentLimitState && limitTriggered) {
      limitTriggered = false;
      digitalWrite(LED_BUILTIN, HIGH);
      Serial.println("[LIMIT] Switch released. Reverse allowed.");
    }

    // ---- Software forward limit ----
    bool wasForwardBlocked = forwardBlocked;
    forwardBlocked = (encoderCount >= maxTravelCounts);

    if (forwardBlocked && !wasForwardBlocked) {
      stopMotor();
      delay(100);
      if (targetCounts < maxTravelCounts) {
        movingToTarget = true;
        resetPID();
        Serial.println("[LIMIT] Soft limit hit - pulling back to target.");
      } else {
        movingToTarget     = false;
        logging            = false;
        currentMoveContext = MOVE_CONTEXT_NONE;
        if (sauceActive) {
          sauceActive     = false;
          saucePhase1Done = false;
          Serial.println("STOP DISPENSING");
        }
        Serial.println("LIMIT TRIGGERED");
        Serial.print("[LIMIT] Max travel reached (");
        Serial.print(maxTravelInches, 1);
        Serial.println(" in) - forward blocked.");
      }
    } else if (!forwardBlocked && wasForwardBlocked) {
      Serial.println("[LIMIT] Back within range. Forward allowed.");
    }

    // ---- Enforce travel limits ----
    if (limitTriggered && currentPulse < ESC_STOP) { stopMotor(); movingToTarget = false; }
    if (forwardBlocked && currentPulse > ESC_STOP && targetCounts >= maxTravelCounts) {
      stopMotor(); movingToTarget = false;
    }

    // ================================================================
    // SAUCE SEQUENCE STATE MACHINE
    // ================================================================
    if (sauceActive && !movingToTarget) {
      if (!saucePhase1Done) {
        saucePhase1Done = true;
        Serial.println("DISPENSING");
        Serial.print("[SAUCE] Dispensing started at ");
        Serial.print(encoderCount / COUNTS_PER_INCH, 3);
        Serial.println(" in");
        startMove(SAUCE_END_INCHES, sauceSpeed, MOVE_CONTEXT_SAUCE2);

      } else {
        sauceActive        = false;
        saucePhase1Done    = false;
        currentMoveContext = MOVE_CONTEXT_NONE;
        Serial.println("STOP DISPENSING");
        Serial.println("SAUCE COMPLETE");
        Serial.print("[SAUCE] Sequence complete. Final position: ");
        Serial.print(encoderCount / COUNTS_PER_INCH, 3);
        Serial.println(" in");
      }
    }

    // ================================================================
    // CASCADED PID
    // ================================================================
    if (movingToTarget) {

      float posError = targetCounts - encoderCount;

      // ---- Check arrival ----
      if (abs(posError) <= POS_DEADBAND) {
        stopMotor();
        movingToTarget       = false;
        logging              = false;
        desiredSpeedSetpoint = 0;

        // ---- Output clean completion string based on context ----
        switch (currentMoveContext) {
          case MOVE_CONTEXT_GOTO:
            Serial.println("GOTO COMPLETE");
            Serial.print("[GOTO] Arrived at ");
            Serial.print(encoderCount / COUNTS_PER_INCH, 3);
            Serial.print(" in | Error: ");
            Serial.print(posError / COUNTS_PER_INCH, 4);
            Serial.print(" in | Time: ");
            Serial.print(millis() - moveStartTime);
            Serial.println("ms");
            currentMoveContext = MOVE_CONTEXT_NONE;
            break;

          case MOVE_CONTEXT_DOCK:
            Serial.println("DOCK COMPLETE");
            Serial.print("[DOCK] Arrived at ");
            Serial.print(encoderCount / COUNTS_PER_INCH, 3);
            Serial.println(" in");
            currentMoveContext = MOVE_CONTEXT_NONE;
            break;

          case MOVE_CONTEXT_SAUCE1:
            // Phase 1 done — sauce state machine takes over next loop
            // do not print completion here
            currentMoveContext = MOVE_CONTEXT_NONE;
            break;

          case MOVE_CONTEXT_SAUCE2:
            // Phase 2 done — sauce state machine handles output next loop
            currentMoveContext = MOVE_CONTEXT_NONE;
            break;

          default:
            currentMoveContext = MOVE_CONTEXT_NONE;
            break;
        }

      } else {

        if (posError > 0 && forwardBlocked && targetCounts >= maxTravelCounts) {
          stopMotor(); movingToTarget = false; logging = false;
          if (sauceActive) { sauceActive = false; saucePhase1Done = false; Serial.println("STOP DISPENSING"); }
          currentMoveContext = MOVE_CONTEXT_NONE;
          Serial.println("[GOTO] Aborted - forward limit.");

        } else if (posError < 0 && limitTriggered) {
          stopMotor(); movingToTarget = false; logging = false;
          if (sauceActive) { sauceActive = false; saucePhase1Done = false; Serial.println("STOP DISPENSING"); }
          currentMoveContext = MOVE_CONTEXT_NONE;
          Serial.println("[GOTO] Aborted - home limit.");

        } else {

          // ---- OUTER LOOP: Position → Speed setpoint ----
          if (now - lastPosLoop >= POS_LOOP_MS) {
            lastPosLoop = now;

            float posErrorInches = posError / COUNTS_PER_INCH;
            pos_integral += posErrorInches;
            pos_integral = constrain(pos_integral, -10.0, 10.0);
            float pos_derivative = posErrorInches - pos_lastError;
            pos_lastError = posErrorInches;

            float rawSpeedSetpoint = pos_Kp * posErrorInches
                                   + pos_Ki * pos_integral
                                   + pos_Kd * pos_derivative;

            desiredSpeedSetpoint = constrain(rawSpeedSetpoint, -maxMoveSpeed, maxMoveSpeed);
          }

          // ---- INNER LOOP: Speed → ESC pulse ----
          if (now - lastSpdLoop >= SPD_LOOP_MS) {
            lastSpdLoop = now;

            float spdError = desiredSpeedSetpoint - currentSpeedInchesPerSec;
            spd_integral += spdError;
            spd_integral = constrain(spd_integral, -200.0, 200.0);
            float spd_derivative = spdError - spd_lastError;
            spd_lastError = spdError;

            float spdOutput = spd_Kp * spdError
                            + spd_Ki * spd_integral
                            + spd_Kd * spd_derivative;

            spdOutput = constrain(spdOutput, -spdClamp, spdClamp);

            if (abs(spdOutput) < MIN_PULSE_OFFSET
                && abs(posError) > POS_DEADBAND
                && abs(currentSpeedInchesPerSec) < 0.3) {
              spdOutput = (desiredSpeedSetpoint > 0) ? MIN_PULSE_OFFSET : -MIN_PULSE_OFFSET;
            }

            if (abs(desiredSpeedSetpoint) < 0.05 && abs(posError) <= POS_DEADBAND * 2) {
              spdOutput = 0;
            }

            currentPulse = ESC_STOP + (int)spdOutput;
            currentPulse = constrain(currentPulse, ESC_MIN, ESC_MAX);
            esc.writeMicroseconds(currentPulse);
          }
        }
      }
    } else {
      desiredSpeedSetpoint = 0;
    }
  }

  // ---- Live logging ----
  if (logging) {
    Serial.print(now - moveStartTime);
    Serial.print(",");
    Serial.print(encoderCount / COUNTS_PER_INCH, 4);
    Serial.print(",");
    Serial.print(targetCounts / COUNTS_PER_INCH, 4);
    Serial.print(",");
    Serial.print((targetCounts - encoderCount) / COUNTS_PER_INCH, 4);
    Serial.print(",");
    Serial.print(currentSpeedInchesPerSec, 4);
    Serial.print(",");
    Serial.print(desiredSpeedSetpoint, 4);
    Serial.print(",");
    Serial.println(currentPulse);

  } else {
    if (now - lastPrint >= statusInterval) {
      lastPrint = now;
      Serial.print("[STATUS] Pos: ");
      Serial.print(encoderCount / COUNTS_PER_INCH, 3);
      Serial.print(" in | ActSpd: ");
      Serial.print(currentSpeedInchesPerSec, 2);
      Serial.print(" in/s | SpdSP: ");
      Serial.print(desiredSpeedSetpoint, 2);
      Serial.print(" in/s | Tgt: ");
      Serial.print(targetCounts / COUNTS_PER_INCH, 3);
      Serial.print(" in | Moving: ");
      Serial.print(movingToTarget ? "YES" : "no");
      Serial.print(" | Sauce: ");
      Serial.print(sauceActive ? "ACTIVE" : "off");
      Serial.print(" | ESC: ");
      Serial.print(currentPulse);
      Serial.println("us");
    }
  }

  // ---- Serial commands ----
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toUpperCase();

    if (cmd == "STOP" || cmd == "S") {
      stopMotor();
      movingToTarget     = false;
      homingActive       = false;
      logging            = false;
      desiredSpeedSetpoint = 0;
      currentMoveContext = MOVE_CONTEXT_NONE;
      if (sauceActive) {
        sauceActive     = false;
        saucePhase1Done = false;
        Serial.println("STOP DISPENSING");
      }
      Serial.println("STOPPED");
      Serial.println("[ESC] Stopped.");

    } else if (cmd == "ZERO") {
      logging = false;
      if (sauceActive) {
        sauceActive     = false;
        saucePhase1Done = false;
        Serial.println("STOP DISPENSING");
      }
      currentMoveContext = MOVE_CONTEXT_NONE;
      Serial.println("[ZERO] Starting homing routine...");
      runHomingRoutine();

    } else if (cmd == "DOCK") {
      if (DOCK_POSITION_INCHES > maxTravelInches) {
        Serial.println("[ERR] Dock position exceeds soft limit.");
      } else if (limitTriggered && DOCK_POSITION_INCHES < (encoderCount / COUNTS_PER_INCH)) {
        Serial.println("[ERR] Cannot move reverse - home limit triggered.");
      } else {
        sauceActive     = false;
        saucePhase1Done = false;
        startMove(DOCK_POSITION_INCHES, maxMoveSpeed, MOVE_CONTEXT_DOCK);
        Serial.print("[DOCK] Moving to dock position: ");
        Serial.print(DOCK_POSITION_INCHES, 1);
        Serial.println(" in");
      }

    } else if (cmd.startsWith("SAUCE")) {
      float spd = cmd.substring(5).toFloat();
      if (spd <= 0.0) {
        Serial.println("[ERR] Invalid sauce speed. Must be > 0.");
      } else if (movingToTarget) {
        Serial.println("[ERR] Gantry is already moving. Wait or STOP first.");
      } else {
        sauceSpeed      = spd;
        sauceActive     = true;
        saucePhase1Done = false;
        Serial.print("[SAUCE] Sequence started at ");
        Serial.print(sauceSpeed, 2);
        Serial.println(" in/s dispensing speed.");
        Serial.print("[SAUCE] Moving to start position ");
        Serial.print(SAUCE_START_INCHES, 2);
        Serial.println(" in...");
        startMove(SAUCE_START_INCHES, maxMoveSpeed, MOVE_CONTEXT_SAUCE1);
      }

    } else if (cmd.startsWith("GOTO")) {
      float inches = cmd.substring(4).toFloat();
      if (inches < 0.0 || inches > maxTravelInches) {
        Serial.print("[ERR] Out of range. Valid: 0.0 - ");
        Serial.print(maxTravelInches, 1); Serial.println(" in");
      } else if (limitTriggered && inches < (encoderCount / COUNTS_PER_INCH)) {
        Serial.println("[ERR] Cannot move reverse - home limit triggered.");
      } else if (forwardBlocked && inches > (encoderCount / COUNTS_PER_INCH)) {
        Serial.println("[ERR] Cannot move forward - soft limit reached.");
      } else {
        sauceActive     = false;
        saucePhase1Done = false;
        startMove(inches, maxMoveSpeed, MOVE_CONTEXT_GOTO);
        Serial.print("[GOTO] Moving to ");
        Serial.print(inches, 3);
        Serial.print(" in | Max speed: ");
        Serial.print(maxMoveSpeed, 2);
        Serial.println(" in/s");
      }

    } else if (cmd.startsWith("SLIM")) {
      float slim = cmd.substring(4).toFloat();
      if (slim <= 0.0 || slim > 30.0) {
        Serial.println("[ERR] Soft limit out of range. Valid: 0.1 - 30.0 in");
      } else {
        maxTravelInches = slim;
        maxTravelCounts = (long)(slim * COUNTS_PER_INCH);
        Serial.print("[LIMIT] Soft limit set to ");
        Serial.print(maxTravelInches, 3);
        Serial.println(" in");
      }

    } else if (cmd.startsWith("MSPD")) {
      float spd = cmd.substring(4).toFloat();
      spd = constrain(spd, 0.1, MAX_SPEED_HARD_CAP);
      maxMoveSpeed = spd;
      Serial.print("[SPEED] Max move speed = ");
      Serial.print(maxMoveSpeed, 2); Serial.println(" in/s");

    } else if (cmd.startsWith("PKP")) {
      pos_Kp = cmd.substring(3).toFloat();
      Serial.print("[POS PID] Kp = "); Serial.println(pos_Kp, 4);

    } else if (cmd.startsWith("PKI")) {
      pos_Ki = cmd.substring(3).toFloat();
      pos_integral = 0;
      Serial.print("[POS PID] Ki = "); Serial.println(pos_Ki, 4);

    } else if (cmd.startsWith("PKD")) {
      pos_Kd = cmd.substring(3).toFloat();
      Serial.print("[POS PID] Kd = "); Serial.println(pos_Kd, 4);

    } else if (cmd.startsWith("SKP")) {
      spd_Kp = cmd.substring(3).toFloat();
      Serial.print("[SPD PID] Kp = "); Serial.println(spd_Kp, 4);

    } else if (cmd.startsWith("SKI")) {
      spd_Ki = cmd.substring(3).toFloat();
      spd_integral = 0;
      Serial.print("[SPD PID] Ki = "); Serial.println(spd_Ki, 4);

    } else if (cmd.startsWith("SKD")) {
      spd_Kd = cmd.substring(3).toFloat();
      Serial.print("[SPD PID] Kd = "); Serial.println(spd_Kd, 4);

    } else if (cmd.startsWith("CLAMP")) {
      spdClamp = cmd.substring(5).toInt();
      spdClamp = constrain(spdClamp, 10, 500);
      Serial.print("[SPD PID] Clamp = "); Serial.println(spdClamp);

    } else if (cmd.startsWith("SINT")) {
      statusInterval = cmd.substring(4).toInt();
      statusInterval = constrain(statusInterval, 50, 5000);
      Serial.print("[STATUS] Interval = "); Serial.print(statusInterval); Serial.println("ms");

    } else if (cmd == "LOG") {
      logging = !logging;
      moveStartTime = millis();
      if (logging) {
        Serial.println("[LOG] Logging ON");
        Serial.println("ms,pos_in,target_in,error_in,act_spd,spd_sp,esc_us");
      } else {
        Serial.println("[LOG] Logging OFF");
      }

    } else if (cmd.startsWith("FWD")) {
      if (forwardBlocked) {
        Serial.println("[ERR] Forward blocked - soft limit reached.");
      } else {
        movingToTarget       = false;
        logging              = false;
        desiredSpeedSetpoint = 0;
        sauceActive          = false;
        saucePhase1Done      = false;
        currentMoveContext   = MOVE_CONTEXT_NONE;
        resetPID();
        int power = constrain(cmd.substring(3).toInt(), 0, 100);
        currentPulse = map(power, 0, 100, 1500, 2000);
        currentPulse = constrain(currentPulse, ESC_MIN, ESC_MAX);
        esc.writeMicroseconds(currentPulse);
        Serial.print("[ESC] Forward "); Serial.print(power);
        Serial.print("% - Pulse: "); Serial.print(currentPulse);
        Serial.print("us | Pos: "); Serial.print(encoderCount / COUNTS_PER_INCH, 3);
        Serial.println(" in");
      }

    } else if (cmd.startsWith("REV")) {
      if (limitTriggered) {
        Serial.println("[ERR] Reverse blocked - home limit triggered.");
      } else {
        movingToTarget       = false;
        logging              = false;
        desiredSpeedSetpoint = 0;
        sauceActive          = false;
        saucePhase1Done      = false;
        currentMoveContext   = MOVE_CONTEXT_NONE;
        resetPID();
        int power = constrain(cmd.substring(3).toInt(), 0, 100);
        currentPulse = map(power, 0, 100, 1500, 1000);
        currentPulse = constrain(currentPulse, ESC_MIN, ESC_MAX);
        esc.writeMicroseconds(currentPulse);
        Serial.print("[ESC] Reverse "); Serial.print(power);
        Serial.print("% - Pulse: "); Serial.print(currentPulse);
        Serial.print("us | Pos: "); Serial.print(encoderCount / COUNTS_PER_INCH, 3);
        Serial.println(" in");
      }

    } else if (cmd == "POS" || cmd == "P") {
      Serial.print("[POS] ");
      Serial.print(encoderCount / COUNTS_PER_INCH, 4);
      Serial.print(" in ("); Serial.print(encoderCount);
      Serial.print(" counts) | ActSpd: ");
      Serial.print(currentSpeedInchesPerSec, 3);
      Serial.print(" in/s | FWD: "); Serial.print(forwardBlocked ? "BLOCKED" : "ok");
      Serial.print(" | REV: "); Serial.println(limitTriggered ? "BLOCKED" : "ok");

    } else if (cmd == "DIAG" || cmd == "D") {
      Serial.println("------------------------------");
      Serial.println("[DIAG] System status:");
      Serial.print("  Position       : "); Serial.print(encoderCount / COUNTS_PER_INCH, 4); Serial.println(" in");
      Serial.print("  Encoder count  : "); Serial.println(encoderCount);
      Serial.print("  Target         : "); Serial.print(targetCounts / COUNTS_PER_INCH, 3); Serial.println(" in");
      Serial.print("  Actual speed   : "); Serial.print(currentSpeedInchesPerSec, 3); Serial.println(" in/s");
      Serial.print("  Speed setpoint : "); Serial.print(desiredSpeedSetpoint, 3); Serial.println(" in/s");
      Serial.print("  Max move speed : "); Serial.print(maxMoveSpeed, 2); Serial.println(" in/s");
      Serial.print("  Hard speed cap : "); Serial.print(MAX_SPEED_HARD_CAP, 1); Serial.println(" in/s");
      Serial.print("  Soft limit     : "); Serial.print(maxTravelInches, 3); Serial.println(" in");
      Serial.print("  Dock position  : "); Serial.print(DOCK_POSITION_INCHES, 1); Serial.println(" in");
      Serial.print("  Sauce start    : "); Serial.print(SAUCE_START_INCHES, 2); Serial.println(" in");
      Serial.print("  Sauce end      : "); Serial.print(SAUCE_END_INCHES, 2); Serial.println(" in");
      Serial.print("  Sauce speed    : "); Serial.print(sauceSpeed, 2); Serial.println(" in/s");
      Serial.print("  Sauce active   : "); Serial.println(sauceActive ? "YES" : "no");
      Serial.print("  Moving         : "); Serial.println(movingToTarget ? "YES" : "no");
      Serial.print("  Homing active  : "); Serial.println(homingActive ? "YES" : "no");
      Serial.print("  Home limit sw  : "); Serial.println(limitTriggered ? "TRIGGERED" : "clear");
      Serial.print("  Forward        : "); Serial.println(forwardBlocked ? "BLOCKED" : "allowed");
      Serial.print("  Reverse        : "); Serial.println(limitTriggered ? "BLOCKED" : "allowed");
      Serial.print("  ESC pulse      : "); Serial.print(currentPulse); Serial.println("us");
      Serial.print("  Spd clamp      : "); Serial.println(spdClamp);
      Serial.print("  Min pulse off  : "); Serial.println(MIN_PULSE_OFFSET);
      Serial.print("  Status intv    : "); Serial.print(statusInterval); Serial.println("ms");
      Serial.println("  --- Position PID ---");
      Serial.print("  pos Kp/Ki/Kd   : "); Serial.print(pos_Kp); Serial.print(" / "); Serial.print(pos_Ki); Serial.print(" / "); Serial.println(pos_Kd);
      Serial.println("  --- Speed PID ---");
      Serial.print("  spd Kp/Ki/Kd   : "); Serial.print(spd_Kp); Serial.print(" / "); Serial.print(spd_Ki); Serial.print(" / "); Serial.println(spd_Kd);
      Serial.print("  Counts/inch    : "); Serial.println(COUNTS_PER_INCH);
      Serial.print("  Free heap      : "); Serial.print(ESP.getFreeHeap()); Serial.println(" bytes");
      Serial.print("  Uptime         : "); Serial.print(millis() / 1000); Serial.println("s");
      Serial.println("------------------------------");

    } else if (cmd == "HELP" || cmd == "H") {
      Serial.println("------------------------------");
      Serial.println("  GOTO<in>      Go to position");
      Serial.println("  DOCK          Go to dock (14.0 in)");
      Serial.println("  SAUCE<in/s>   Run sauce sequence");
      Serial.println("  FWD<0-100>    Forward power");
      Serial.println("  REV<0-100>    Reverse power");
      Serial.println("  MSPD<in/s>    Max move speed");
      Serial.println("  SLIM<in>      Set soft limit");
      Serial.println("  PKP/PKI/PKD   Position PID gains");
      Serial.println("  SKP/SKI/SKD   Speed PID gains");
      Serial.println("  CLAMP<val>    Speed loop clamp");
      Serial.println("  SINT<ms>      Status print interval");
      Serial.println("  LOG           Toggle live data");
      Serial.println("  STOP / S      Stop motor");
      Serial.println("  ZERO          Run homing routine");
      Serial.println("  POS  / P      Print position");
      Serial.println("  DIAG / D      Diagnostics");
      Serial.println("  HELP / H      Show commands");
      Serial.println("------------------------------");
      Serial.print("  Soft limit    : "); Serial.print(maxTravelInches, 1); Serial.println(" in");
      Serial.print("  Dock position : "); Serial.print(DOCK_POSITION_INCHES, 1); Serial.println(" in");
      Serial.print("  Sauce start   : "); Serial.print(SAUCE_START_INCHES, 2); Serial.println(" in");
      Serial.print("  Sauce end     : "); Serial.print(SAUCE_END_INCHES, 2); Serial.println(" in");
      Serial.print("  Sauce speed   : "); Serial.print(sauceSpeed, 2); Serial.println(" in/s");
      Serial.print("  Max move speed: "); Serial.print(maxMoveSpeed, 2); Serial.println(" in/s");
      Serial.print("  pos Kp/Ki/Kd  : "); Serial.print(pos_Kp); Serial.print(" / "); Serial.print(pos_Ki); Serial.print(" / "); Serial.println(pos_Kd);
      Serial.print("  spd Kp/Ki/Kd  : "); Serial.print(spd_Kp); Serial.print(" / "); Serial.print(spd_Ki); Serial.print(" / "); Serial.println(spd_Kd);
      Serial.println("------------------------------");

    } else if (cmd.length() > 0) {
      Serial.println("[ERR] Unknown command. Type HELP.");
    }
  }

  delay(2);
}

// =============================================================
// START MOVE HELPER
// =============================================================

void startMove(float inches, float speed, int context) {
  maxMoveSpeed       = max(speed, 0.1f);
  targetCounts       = (long)(inches * COUNTS_PER_INCH);
  movingToTarget     = true;
  moveStartTime      = millis();
  currentMoveContext = context;
  resetPID();
}

// =============================================================
// SPEED MEASUREMENT
// =============================================================

void updateSpeed() {
  unsigned long now = millis();
  unsigned long dt  = now - lastSpeedTime;
  if (dt >= 10) {
    long countNow     = encoderCount;
    long deltaCounts  = countNow - lastSpeedCount;
    float deltaInches = deltaCounts / COUNTS_PER_INCH;
    float dtSec       = dt / 1000.0;
    currentSpeedInchesPerSec = deltaInches / dtSec;
    lastSpeedTime  = now;
    lastSpeedCount = countNow;
  }
}

// =============================================================
// RESET PID STATE
// =============================================================

void resetPID() {
  pos_integral  = 0;
  pos_lastError = 0;
  spd_integral  = 0;
  spd_lastError = 0;
}

// =============================================================
// HOMING ROUTINE
// =============================================================

void runHomingRoutine() {
  homingActive = true;
  Serial.println("[ZERO] ---- Homing routine started ----");

  Serial.println("[ZERO] Phase 1: Reversing to home switch at REV5...");
  int revPulse = map(HOME_PHASE1_POWER, 0, 100, 1500, 1000);
  esc.writeMicroseconds(revPulse);
  currentPulse = revPulse;

  unsigned long timeout = millis() + 60000;
  while (digitalRead(LIMIT_MIN) != LOW) {
    if (millis() > timeout) {
      stopMotor();
      homingActive = false;
      Serial.println("[ZERO] ERROR: Timeout. Aborted.");
      return;
    }
    delay(10);
  }

  stopMotor();
  encoderCount   = 0;
  limitTriggered = true;
  digitalWrite(LED_BUILTIN, LOW);
  Serial.println("[ZERO] Home switch triggered. Position zeroed.");
  delay(300);

  Serial.println("[ZERO] Phase 2: Finding exact zero...");
  int  attempt        = 0;
  bool exactZeroFound = false;

  while (!exactZeroFound) {
    attempt++;
    Serial.print("[ZERO] Attempt "); Serial.println(attempt);

    Serial.println("[ZERO]   FWD5 for 2 seconds...");
    int fwdPulse = map(HOME_FWD_POWER, 0, 100, 1500, 2000);
    esc.writeMicroseconds(fwdPulse);
    currentPulse   = fwdPulse;
    limitTriggered = false;
    digitalWrite(LED_BUILTIN, HIGH);
    delay(HOME_FWD_TIME_MS);
    stopMotor();
    delay(200);
    Serial.print("[ZERO]   Position after forward: ");
    Serial.print(encoderCount / COUNTS_PER_INCH, 3);
    Serial.println(" in");

    Serial.println("[ZERO]   Creeping reverse at REV5...");
    revPulse = map(HOME_REV_POWER, 0, 100, 1500, 1000);
    esc.writeMicroseconds(revPulse);
    currentPulse = revPulse;

    unsigned long creepTimeout = millis() + 15000;
    bool limitHit = false;
    while (millis() < creepTimeout) {
      if (digitalRead(LIMIT_MIN) == LOW) {
        stopMotor();
        delay(50);
        limitHit = true;
        break;
      }
      delay(5);
    }

    if (!limitHit) {
      stopMotor();
      Serial.println("[ZERO]   Creep timeout - retrying...");
      encoderCount   = 0;
      limitTriggered = false;
      delay(300);

    } else {
      float posInches = encoderCount / COUNTS_PER_INCH;
      Serial.print("[ZERO]   Limit triggered at: ");
      Serial.print(posInches, 4);
      Serial.println(" in");

      if (abs(posInches) <= HOME_ZERO_TOLERANCE) {
        encoderCount   = 0;
        limitTriggered = true;
        exactZeroFound = true;
        digitalWrite(LED_BUILTIN, LOW);
        Serial.println("[ZERO]   Exact zero confirmed!");
      } else {
        encoderCount   = 0;
        limitTriggered = true;
        Serial.print("[ZERO]   Drifted (");
        Serial.print(posInches, 4);
        Serial.println(" in) - retrying...");
        delay(300);
      }
    }

    if (attempt > 10) {
      stopMotor();
      homingActive = false;
      Serial.println("[ZERO] ERROR: Could not find exact zero after 10 attempts.");
      Serial.println("[ZERO] Setting position to 0 at current location.");
      encoderCount = 0;
      return;
    }
  }

  stopMotor();
  encoderCount   = 0;
  limitTriggered = true;
  homingActive   = false;
  movingToTarget = false;
  resetPID();
  Serial.println("[ZERO] ---- Homing complete ----");
  Serial.println("[ZERO] Position = 0.000 in");
  Serial.println("HOMING COMPLETE");
}

// =============================================================

bool moveToInches(float inches) {
  long target   = (long)(inches * COUNTS_PER_INCH);
  float integ   = 0;
  float lasterr = 0;
  unsigned long timeout = millis() + 15000;

  while (millis() < timeout) {
    updateSpeed();
    float err = target - encoderCount;
    if (abs(err) <= POS_DEADBAND) { stopMotor(); return true; }
    if (digitalRead(LIMIT_MIN) == LOW && err < 0) { stopMotor(); encoderCount = 0; return false; }
    integ += err;
    integ = constrain(integ, -5000, 5000);
    float output = 0.3 * err + 0.001 * integ + 0.1 * (err - lasterr);
    output = constrain(output, -300, 300);
    if (abs(output) < MIN_PULSE_OFFSET && abs(currentSpeedInchesPerSec) < 0.3) {
      output = (err > 0) ? MIN_PULSE_OFFSET : -MIN_PULSE_OFFSET;
    }
    esc.writeMicroseconds(ESC_STOP + (int)output);
    currentPulse = ESC_STOP + (int)output;
    lasterr = err;
    delay(20);
  }
  stopMotor();
  return false;
}

bool waitForMotion(int timeoutMs) {
  long startCount = encoderCount;
  unsigned long deadline = millis() + timeoutMs;
  while (millis() < deadline) {
    if (abs(encoderCount - startCount) > 20) return true;
    delay(10);
  }
  return false;
}

void stopMotor() {
  esc.writeMicroseconds(ESC_STOP);
  currentPulse         = ESC_STOP;
  desiredSpeedSetpoint = 0;
  resetPID();
}

void IRAM_ATTR updateEncoder() {
  byte MSB = digitalRead(ENC_A);
  byte LSB = digitalRead(ENC_B);
  byte encoded = (MSB << 1) | LSB;
  byte sum = (lastEncoded << 2) | encoded;
  if (sum == 0b1101 || sum == 0b0100 || sum == 0b0010 || sum == 0b1011) encoderCount++;
  if (sum == 0b1110 || sum == 0b0111 || sum == 0b0001 || sum == 0b1000) encoderCount--;
  lastEncoded = encoded;
}