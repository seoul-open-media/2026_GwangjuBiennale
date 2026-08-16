#include <Arduino.h>
#include <SPI.h>
#include <ACAN2517FD.h>
#include <Moteus.h>

#define XBEE Serial1

static constexpr uint8_t START_B = 255;
static constexpr uint8_t END_B = 254;
static constexpr uint8_t BELL_SHAKER_FLAG = 0xB1;

// Bell_shaker: single motor on Moteus R4
// Serial command: '1' starts spinning, '0' stops.

static constexpr uint8_t MOTEUS_ID = 1;
static constexpr uint8_t BUS1_CS = 10;
static constexpr float RUN_VELOCITY_TURNS_PER_SEC = 2.8f;
static constexpr float VELOCITY_LIMIT_BASE = 6.5f;
static constexpr float ACCEL_LIMIT_BASE = 35.0f;
static constexpr float MAX_TORQUE_BASE = 3.5f;
static constexpr float VELOCITY_LIMIT_FAST = 22.0f;
static constexpr float ACCEL_LIMIT_FAST = 450.0f;
static constexpr float MAX_TORQUE_FAST = 14.0f;
static constexpr float VELOCITY_LIMIT_EXTREME = 30.0f;
static constexpr float ACCEL_LIMIT_EXTREME = 800.0f;
static constexpr float MAX_TORQUE_EXTREME = 18.0f;
static constexpr uint32_t COMMAND_PERIOD_MS = 8;
static constexpr uint32_t STOP_BRAKE_MS = 300;
static constexpr uint32_t DISENGAGE_RETRY_MS = 500;
static constexpr float PRESET8_GEAR_RATIO = 20.0f;
static constexpr float PRESET8_OUTPUT_SPEED_BASE = 1.1f;
static constexpr uint32_t PRESET8_REVERSE_PERIOD_MS = 125;
static constexpr float PRESET1_Q_CURRENT_A = 1.8f;
static constexpr float PRESET1_STARTUP_Q_CURRENT_A = 2.4f;
static constexpr uint32_t PRESET1_STARTUP_BOOST_MS = 250;
static constexpr float PRESET2_3_Q_CURRENT_A = 1.8f;
static constexpr float PRESET4_6_Q_CURRENT_A = 2.4f;
static constexpr float PRESET7_Q_CURRENT_A = 2.8f;
static constexpr float PRESET1_7_MAX_TORQUE = 2.5f;
static constexpr float PRESET1_MAX_TORQUE = 1.8f;
static constexpr float PRESET1_REVERSE_HZ_MIN = 0.7f;
static constexpr float PRESET1_REVERSE_HZ_MAX = 1.4f;
static constexpr float PRESET2_REVERSE_HZ_MIN = 2.5f;
static constexpr float PRESET2_REVERSE_HZ_MAX = 6.0f;
static constexpr float PRESET3_REVERSE_HZ_MIN = 3.0f;
static constexpr float PRESET3_REVERSE_HZ_MAX = 7.0f;
static constexpr float PRESET4_REVERSE_HZ_MIN = 3.5f;
static constexpr float PRESET4_REVERSE_HZ_MAX = 8.5f;
static constexpr float PRESET5_REVERSE_HZ_MIN = 4.0f;
static constexpr float PRESET5_REVERSE_HZ_MAX = 10.0f;
static constexpr float PRESET6_REVERSE_HZ_MIN = 4.5f;
static constexpr float PRESET6_REVERSE_HZ_MAX = 12.0f;
static constexpr float PRESET7_REVERSE_HZ_MIN = 5.0f;
static constexpr float PRESET7_REVERSE_HZ_MAX = 15.0f;
static constexpr uint32_t PRESET1_7_SWEEP_CYCLE_MS = 20000;
static constexpr float PRESET8_Q_CURRENT_A = 4.0f;
static constexpr float PRESET8_MAX_TORQUE = 3.0f;
static constexpr float PRESET9_REVERSE_HZ_MIN = 3.0f;
static constexpr float PRESET9_REVERSE_HZ_MAX = 15.0f;
static constexpr uint32_t PRESET9_SWEEP_CYCLE_MS = 20000;

ACAN2517FD canBus(BUS1_CS, SPI, 255);
Moteus::PositionMode::Format positionFmt;

static Moteus::Options makeMoteusOptions(uint8_t id) {
  Moteus::Options opt;
  opt.id = id;
  opt.query_format.abs_position = Moteus::kFloat;
  opt.query_format.motor_temperature = Moteus::kInt8;
  opt.query_format.temperature = Moteus::kInt8;
  opt.query_format.fault = Moteus::kInt8;
  return opt;
}

static bool getSweepConfig(uint8_t preset, float &hzMin, float &hzMax, uint32_t &cycleMs) {
  switch (preset) {
    case 1:
      hzMin = PRESET1_REVERSE_HZ_MIN;
      hzMax = PRESET1_REVERSE_HZ_MAX;
      cycleMs = PRESET1_7_SWEEP_CYCLE_MS;
      return true;
    case 2:
      hzMin = PRESET2_REVERSE_HZ_MIN;
      hzMax = PRESET2_REVERSE_HZ_MAX;
      cycleMs = PRESET1_7_SWEEP_CYCLE_MS;
      return true;
    case 3:
      hzMin = PRESET3_REVERSE_HZ_MIN;
      hzMax = PRESET3_REVERSE_HZ_MAX;
      cycleMs = PRESET1_7_SWEEP_CYCLE_MS;
      return true;
    case 4:
      hzMin = PRESET4_REVERSE_HZ_MIN;
      hzMax = PRESET4_REVERSE_HZ_MAX;
      cycleMs = PRESET1_7_SWEEP_CYCLE_MS;
      return true;
    case 5:
      hzMin = PRESET5_REVERSE_HZ_MIN;
      hzMax = PRESET5_REVERSE_HZ_MAX;
      cycleMs = PRESET1_7_SWEEP_CYCLE_MS;
      return true;
    case 6:
      hzMin = PRESET6_REVERSE_HZ_MIN;
      hzMax = PRESET6_REVERSE_HZ_MAX;
      cycleMs = PRESET1_7_SWEEP_CYCLE_MS;
      return true;
    case 7:
      hzMin = PRESET7_REVERSE_HZ_MIN;
      hzMax = PRESET7_REVERSE_HZ_MAX;
      cycleMs = PRESET1_7_SWEEP_CYCLE_MS;
      return true;
    case 9:
      hzMin = PRESET9_REVERSE_HZ_MIN;
      hzMax = PRESET9_REVERSE_HZ_MAX;
      cycleMs = PRESET9_SWEEP_CYCLE_MS;
      return true;
    default:
      return false;
  }
}

Moteus motor(canBus, makeMoteusOptions(MOTEUS_ID));

bool motorRunning = false;
uint32_t lastCmdMs = 0;
bool canReady = false;
char cmdBuf[16];
uint8_t cmdIdx = 0;
uint8_t xbeePacket[4];
uint8_t xbeeIdx = 0;
uint32_t stopRequestedMs = 0;
uint32_t lastDisengageTryMs = 0;
bool disengageDone = false;

uint8_t activePreset = 1;
float activeSpeedMultiplier = 1.0f;
uint32_t reversePeriodMs = 0;
int8_t directionSign = 1;
uint32_t lastDirectionFlipMs = 0;
float activeVelocityLimit = VELOCITY_LIMIT_BASE;
float activeAccelLimit = ACCEL_LIMIT_BASE;
float activeMaxTorque = MAX_TORQUE_BASE;
bool activeUseCurrentMode = false;
float activeQCurrentA = 0.0f;
bool waitingForReverse = false;
uint32_t reverseRequestMs = 0;
uint32_t preset9SweepStartMs = 0;
uint32_t preset1StartMs = 0;
uint8_t preset1FlipCount = 0;
static constexpr float REVERSE_VELOCITY_CONFIRM = 0.15f;
static constexpr uint32_t REVERSE_FORCE_TIMEOUT_MS = 400;

static void sendMoteusVelocity(float velocityTurnsPerSec) {
  if (!canReady) return;

  Moteus::PositionMode::Command cmd;
  cmd.position = NaN;
  cmd.stop_position = NaN;
  cmd.velocity = velocityTurnsPerSec;
  cmd.velocity_limit = activeVelocityLimit;
  cmd.accel_limit = activeAccelLimit;
  cmd.maximum_torque = activeMaxTorque;
  cmd.kp_scale = 1.0f;
  cmd.kd_scale = 1.0f;
  cmd.feedforward_torque = 0.0f;
  cmd.watchdog_timeout = NaN;

  motor.SetPosition(cmd, &positionFmt);
  canBus.poll();
}

static void sendMoteusCurrent(float qCurrentA) {
  if (!canReady) return;

  Moteus::CurrentMode::Command cmd;
  cmd.d_A = 0.0f;
  cmd.q_A = qCurrentA;

  motor.SetCurrent(cmd);
  canBus.poll();
}

static void sendMoteusBrakeZero() {
  if (!canReady) return;

  Moteus::PositionMode::Command cmd;
  cmd.position = NaN;
  cmd.stop_position = NaN;
  cmd.velocity = 0.0f;
  cmd.velocity_limit = activeVelocityLimit;
  cmd.accel_limit = activeAccelLimit;
  cmd.maximum_torque = activeMaxTorque;
  cmd.kp_scale = 1.0f;
  cmd.kd_scale = 1.0f;
  cmd.feedforward_torque = 0.0f;
  cmd.watchdog_timeout = 0.1f;

  motor.SetPosition(cmd, &positionFmt);
  canBus.poll();
}

static void startMotor() {
  motorRunning = true;
  activePreset = 1;
  activeSpeedMultiplier = 1.0f;
  reversePeriodMs = 0;
  directionSign = 1;
  lastDirectionFlipMs = millis();
  activeVelocityLimit = VELOCITY_LIMIT_BASE;
  activeAccelLimit = ACCEL_LIMIT_BASE;
  activeMaxTorque = PRESET1_MAX_TORQUE;
  activeUseCurrentMode = true;
  activeQCurrentA = PRESET1_Q_CURRENT_A;
  waitingForReverse = false;
  reverseRequestMs = 0;
  preset9SweepStartMs = millis();
  preset1StartMs = millis();
  preset1FlipCount = 0;
  stopRequestedMs = 0;
  disengageDone = false;
  sendMoteusCurrent(activeQCurrentA);
  float sweepHzMin = 0.0f;
  float sweepHzMax = 0.0f;
  uint32_t sweepCycleMs = 0;
  getSweepConfig(1, sweepHzMin, sweepHzMax, sweepCycleMs);
  Serial.print("[Bell_shaker] START (cmd=1), current mode qCurrent=");
  Serial.print(activeQCurrentA, 1);
  Serial.print("A, sweep=");
  Serial.print(sweepHzMin, 1);
  Serial.print("~");
  Serial.print(sweepHzMax, 1);
  Serial.print("Hz/");
  Serial.print(sweepCycleMs / 1000);
  Serial.print("s, torque=");
  Serial.println(activeMaxTorque, 1);
}

static void startAlternatingPreset(uint8_t preset, float speedMultiplier, uint32_t periodMs) {
  motorRunning = true;
  activePreset = preset;
  activeSpeedMultiplier = speedMultiplier;
  reversePeriodMs = periodMs;
  directionSign = 1;
  lastDirectionFlipMs = millis();
  activeUseCurrentMode = true;
  activeQCurrentA = PRESET2_3_Q_CURRENT_A;
  if (preset >= 4 && preset <= 6) {
    activeQCurrentA = PRESET4_6_Q_CURRENT_A;
  } else if (preset == 7) {
    activeQCurrentA = PRESET7_Q_CURRENT_A;
  }
  waitingForReverse = false;
  reverseRequestMs = 0;
  preset9SweepStartMs = millis();
  preset1StartMs = 0;
  preset1FlipCount = 0;

  const float targetSpeed = RUN_VELOCITY_TURNS_PER_SEC * speedMultiplier;
  if (preset == 7) {
    activeVelocityLimit = VELOCITY_LIMIT_EXTREME;
    activeAccelLimit = ACCEL_LIMIT_EXTREME;
    activeMaxTorque = PRESET1_7_MAX_TORQUE;
  } else if (preset >= 4) {
    // Fast reversing presets need higher limits to actually reach target speed.
    activeVelocityLimit = VELOCITY_LIMIT_FAST;
    activeAccelLimit = ACCEL_LIMIT_FAST;
    activeMaxTorque = PRESET1_7_MAX_TORQUE;
  } else {
    activeVelocityLimit = VELOCITY_LIMIT_BASE;
    activeAccelLimit = ACCEL_LIMIT_BASE;
    activeMaxTorque = PRESET1_7_MAX_TORQUE;
  }

  if (activeVelocityLimit < targetSpeed * 1.2f) {
    activeVelocityLimit = targetSpeed * 1.2f;
  }

  stopRequestedMs = 0;
  disengageDone = false;

  sendMoteusCurrent(activeQCurrentA);
  float sweepHzMin = 0.0f;
  float sweepHzMax = 0.0f;
  uint32_t sweepCycleMs = 0;
  getSweepConfig(preset, sweepHzMin, sweepHzMax, sweepCycleMs);

  Serial.print("[Bell_shaker] PRESET "); Serial.print(preset);
  Serial.print(" start, current oscillation, speed x"); Serial.print(speedMultiplier, 1);
  Serial.print(", reverse period="); Serial.print(periodMs);
  Serial.print("ms, qCurrent="); Serial.print(activeQCurrentA, 1);
  Serial.print("A, sweep="); Serial.print(sweepHzMin, 1);
  Serial.print("~"); Serial.print(sweepHzMax, 1);
  Serial.print("Hz/"); Serial.print(sweepCycleMs / 1000);
  Serial.print("s, vlim="); Serial.print(activeVelocityLimit, 1);
  Serial.print(", alim="); Serial.print(activeAccelLimit, 1);
  Serial.print(", torque="); Serial.println(activeMaxTorque, 1);
}

static void startPreset8FastOscillation() {
  motorRunning = true;
  activePreset = 8;
  activeSpeedMultiplier = PRESET8_GEAR_RATIO * PRESET8_OUTPUT_SPEED_BASE;
  reversePeriodMs = PRESET8_REVERSE_PERIOD_MS;
  directionSign = 1;
  lastDirectionFlipMs = millis();
  activeUseCurrentMode = true;
  activeQCurrentA = PRESET8_Q_CURRENT_A;
  waitingForReverse = false;
  reverseRequestMs = 0;
  preset9SweepStartMs = 0;
  preset1StartMs = 0;
  preset1FlipCount = 0;

  const float targetSpeed = RUN_VELOCITY_TURNS_PER_SEC * activeSpeedMultiplier;
  activeVelocityLimit = targetSpeed * 1.3f;
  if (activeVelocityLimit < 50.0f) activeVelocityLimit = 50.0f;
  activeAccelLimit = 1800.0f;
  activeMaxTorque = PRESET8_MAX_TORQUE;

  stopRequestedMs = 0;
  disengageDone = false;
  sendMoteusCurrent(activeQCurrentA);

  Serial.print("[Bell_shaker] PRESET 8 start, current oscillation, speed x");
  Serial.print(activeSpeedMultiplier, 1);
  Serial.print(", reverse period="); Serial.print(reversePeriodMs);
  Serial.print("ms, qCurrent="); Serial.print(activeQCurrentA, 1);
  Serial.print("A, vlim="); Serial.print(activeVelocityLimit, 1);
  Serial.print(", alim="); Serial.print(activeAccelLimit, 1);
  Serial.print(", torque="); Serial.println(activeMaxTorque, 1);
}

static void startPreset9GentleOscillation() {
  motorRunning = true;
  activePreset = 9;
  activeSpeedMultiplier = PRESET8_GEAR_RATIO * PRESET8_OUTPUT_SPEED_BASE;
  reversePeriodMs = 1000;
  directionSign = 1;
  lastDirectionFlipMs = millis();
  activeUseCurrentMode = true;
  activeQCurrentA = PRESET8_Q_CURRENT_A;
  waitingForReverse = false;
  reverseRequestMs = 0;
  preset9SweepStartMs = millis();
  preset1StartMs = 0;

  const float targetSpeed = RUN_VELOCITY_TURNS_PER_SEC * activeSpeedMultiplier;
  activeVelocityLimit = targetSpeed * 1.3f;
  if (activeVelocityLimit < 50.0f) activeVelocityLimit = 50.0f;
  activeAccelLimit = 1800.0f;
  activeMaxTorque = PRESET8_MAX_TORQUE;

  stopRequestedMs = 0;
  disengageDone = false;
  sendMoteusCurrent(activeQCurrentA);

  Serial.print("[Bell_shaker] PRESET 9 start, sweep current oscillation, speed x");
  Serial.print(activeSpeedMultiplier, 1);
  Serial.print(", sweep="); Serial.print(PRESET9_REVERSE_HZ_MIN, 1);
  Serial.print("~"); Serial.print(PRESET9_REVERSE_HZ_MAX, 1);
  Serial.print("Hz/"); Serial.print(PRESET9_SWEEP_CYCLE_MS / 1000);
  Serial.print("s, qCurrent="); Serial.print(activeQCurrentA, 1);
  Serial.print("A, vlim="); Serial.print(activeVelocityLimit, 1);
  Serial.print(", alim="); Serial.print(activeAccelLimit, 1);
  Serial.print(", torque="); Serial.println(activeMaxTorque, 1);
}

static void stopMotor() {
  motorRunning = false;
  activePreset = 1;
  activeSpeedMultiplier = 1.0f;
  reversePeriodMs = 0;
  directionSign = 1;
  activeVelocityLimit = VELOCITY_LIMIT_BASE;
  activeAccelLimit = ACCEL_LIMIT_BASE;
  activeMaxTorque = MAX_TORQUE_BASE;
  activeUseCurrentMode = false;
  activeQCurrentA = 0.0f;
  waitingForReverse = false;
  reverseRequestMs = 0;
  preset9SweepStartMs = 0;
  preset1StartMs = 0;
  preset1FlipCount = 0;
  stopRequestedMs = millis();
  lastDisengageTryMs = 0;
  disengageDone = false;
  sendMoteusBrakeZero();
  Serial.println("[Bell_shaker] STOP (cmd=0)");
}

static void sendDisengageDiagnostic() {
  if (!canReady) return;

  const String reply = motor.DiagnosticCommand("d stop", Moteus::kExpectOK);
  if (reply.length() == 0) {
    Serial.println("[Bell_shaker] d stop: no reply/timeout");
  } else {
    Serial.print("[Bell_shaker] d stop reply: ");
    Serial.println(reply);
  }
}

static void processCommandChar(char c) {
  if (c == '1') {
    startMotor();
  } else if (c == '2') {
    startAlternatingPreset(2, 1.0f, 2000);
  } else if (c == '3') {
    startAlternatingPreset(3, 1.0f, 1000);
  } else if (c == '4') {
    startAlternatingPreset(4, 4.0f, 500);
  } else if (c == '5') {
    startAlternatingPreset(5, 4.0f, 500);
  } else if (c == '6') {
    startAlternatingPreset(6, 4.0f, 500);
  } else if (c == '7') {
    startAlternatingPreset(7, 5.0f, 500);
  } else if (c == '8') {
    startPreset8FastOscillation();
  } else if (c == '9') {
    startPreset9GentleOscillation();
  } else if (c == '0') {
    stopMotor();
  } else if (c == 'h' || c == 'H' || c == '?') {
    Serial.println("[Bell_shaker] commands: 1=run, 2..7=alt presets, 8=fast oscillation, 9=gentle oscillation, 0=stop");
  }
}

static void handleSerialCommand() {
  while (Serial.available()) {
    const char c = static_cast<char>(Serial.read());
    Serial.print("[Bell_shaker] RX: '");
    Serial.print(c);
    Serial.println("'");

    // Quick path: single-char command works immediately.
    if ((c >= '0' && c <= '9') || c == 'h' || c == 'H' || c == '?') {
      processCommandChar(c);
      continue;
    }

    // Also support line-based input such as "1\r\n".
    if (c == '\r' || c == '\n') {
      if (cmdIdx > 0) {
        cmdBuf[cmdIdx] = '\0';
        processCommandChar(cmdBuf[0]);
        cmdIdx = 0;
      }
      continue;
    }

    if (cmdIdx < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdIdx++] = c;
    } else {
      cmdIdx = 0;
    }
  }
}

static void processXBeeCommand(uint8_t cmd) {
  if (cmd <= 9) {
    processCommandChar(static_cast<char>('0' + cmd));
    return;
  }

  Serial.print("[Bell_shaker][XBEE] unsupported cmd: ");
  Serial.println(cmd);
}

static void handleXBeeCommand() {
  while (XBEE.available() > 0) {
    const uint8_t b = static_cast<uint8_t>(XBEE.read());

    if (xbeeIdx == 0) {
      if (b == START_B) {
        xbeePacket[xbeeIdx++] = b;
      }
      continue;
    }

    xbeePacket[xbeeIdx++] = b;

    if (xbeeIdx < sizeof(xbeePacket)) continue;

    xbeeIdx = 0;
    if (xbeePacket[0] != START_B || xbeePacket[3] != END_B) {
      Serial.println("[Bell_shaker][XBEE] bad frame");
      continue;
    }

    const uint8_t flag = xbeePacket[1];
    const uint8_t cmd = xbeePacket[2];
    if (flag != BELL_SHAKER_FLAG) {
      continue;
    }

    Serial.print("[Bell_shaker][XBEE] flag=0x");
    Serial.print(flag, HEX);
    Serial.print(" cmd=");
    Serial.println(cmd);
    processXBeeCommand(cmd);
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 2000) {}
  XBEE.begin(115200);

  SPI.begin();

  ACAN2517FDSettings settings(
      ACAN2517FDSettings::OSC_40MHz,
      1000ll * 1000ll,
      DataBitRateFactor::x1);
  settings.mArbitrationSJW = 2;
  settings.mDriverTransmitFIFOSize = 20;
  settings.mDriverReceiveFIFOSize = 20;

  const uint32_t errorCode = canBus.begin(settings, NULL);
  if (errorCode == 0) {
    canReady = true;
    Serial.println("[Bell_shaker] CAN-FD ready");
  } else {
    Serial.print("[Bell_shaker] CAN-FD init error: 0x");
    Serial.println(errorCode, HEX);
  }

  positionFmt.position = Moteus::kInt16;
  positionFmt.velocity = Moteus::kInt16;
  positionFmt.maximum_torque = Moteus::kInt16;
  positionFmt.feedforward_torque = Moteus::kInt16;
  positionFmt.kp_scale = Moteus::kInt16;
  positionFmt.kd_scale = Moteus::kInt16;
  positionFmt.stop_position = Moteus::kInt16;
  positionFmt.velocity_limit = Moteus::kInt16;
  positionFmt.accel_limit = Moteus::kInt16;

  stopMotor();
  Serial.println("[Bell_shaker] boot complete");
  Serial.println("[Bell_shaker] enter 1 to start, 0 to stop");
  Serial.print("[Bell_shaker] XBee frame: [255][0x");
  Serial.print(BELL_SHAKER_FLAG, HEX);
  Serial.println("][cmd][254], cmd=0..9");
}

void loop() {
  handleSerialCommand();
  handleXBeeCommand();
  if (canReady) {
    canBus.poll();
  }

  // Re-send command periodically.
  const uint32_t now = millis();
  if ((now - lastCmdMs) >= COMMAND_PERIOD_MS) {
    lastCmdMs = now;
    if (motorRunning) {
      if (activeUseCurrentMode) {
        uint32_t activeReversePeriodMs = reversePeriodMs;
        if (preset9SweepStartMs > 0) {
          float sweepHzMin = 0.0f;
          float sweepHzMax = 0.0f;
          uint32_t sweepCycleMs = 0;
          if (getSweepConfig(activePreset, sweepHzMin, sweepHzMax, sweepCycleMs) &&
              sweepCycleMs > 0) {
            const uint32_t phaseMs = (now - preset9SweepStartMs) % sweepCycleMs;
            const float phase = static_cast<float>(phaseMs) /
                                static_cast<float>(sweepCycleMs);
            const float tri = (phase < 0.5f) ? (phase * 2.0f) : ((1.0f - phase) * 2.0f);
            const float hz = sweepHzMin * powf(sweepHzMax / sweepHzMin, tri);
            activeReversePeriodMs = static_cast<uint32_t>(1000.0f / hz);
            if (activeReversePeriodMs == 0) activeReversePeriodMs = 1;
          }
        }

        if (activeReversePeriodMs > 0 && (now - lastDirectionFlipMs) >= activeReversePeriodMs) {
          lastDirectionFlipMs = now;
          directionSign = -directionSign;
          if (activePreset == 1) {
            ++preset1FlipCount;
            // Preset 1: run exactly one round trip (forward -> reverse -> stop).
            if (preset1FlipCount >= 2) {
              stopMotor();
              return;
            }
          }
        }
        float commandQCurrentA = activeQCurrentA;
        if (activePreset == 1 && preset1StartMs > 0 &&
            (now - preset1StartMs) < PRESET1_STARTUP_BOOST_MS) {
          commandQCurrentA = PRESET1_STARTUP_Q_CURRENT_A;
        }
        sendMoteusCurrent(commandQCurrentA * static_cast<float>(directionSign));
      } else {
        if (activePreset >= 2 && reversePeriodMs > 0) {
        if (!waitingForReverse && (now - lastDirectionFlipMs) >= reversePeriodMs) {
          directionSign = -directionSign;
          waitingForReverse = true;
          reverseRequestMs = now;
          Serial.print("[Bell_shaker] reverse request, sign=");
          Serial.println(directionSign > 0 ? "+" : "-");
        }

        if (waitingForReverse) {
          const float measuredVelocity = motor.last_result().values.velocity;
          const bool signMatched =
              (directionSign > 0 && measuredVelocity > REVERSE_VELOCITY_CONFIRM) ||
              (directionSign < 0 && measuredVelocity < -REVERSE_VELOCITY_CONFIRM);

          if (signMatched || (now - reverseRequestMs) >= REVERSE_FORCE_TIMEOUT_MS) {
            waitingForReverse = false;
            lastDirectionFlipMs = now;
            if (signMatched) {
              Serial.print("[Bell_shaker] reverse confirmed, vel=");
              Serial.println(measuredVelocity, 3);
            } else {
              Serial.println("[Bell_shaker] reverse timeout, forcing next schedule");
            }
          }
        }
      }

        const float targetVelocity =
            RUN_VELOCITY_TURNS_PER_SEC * activeSpeedMultiplier * static_cast<float>(directionSign);
        sendMoteusVelocity(targetVelocity);
      }
    } else {
      if (canReady && stopRequestedMs > 0 && (now - stopRequestedMs) < STOP_BRAKE_MS) {
        // First, actively command zero velocity for a short window.
        sendMoteusBrakeZero();
      } else if (canReady) {
        // Then disengage torque via diagnostic command: d stop.
        if (!disengageDone || (now - lastDisengageTryMs) >= DISENGAGE_RETRY_MS) {
          lastDisengageTryMs = now;
          sendDisengageDiagnostic();
          disengageDone = true;
        }
      }
    }
  }
}
