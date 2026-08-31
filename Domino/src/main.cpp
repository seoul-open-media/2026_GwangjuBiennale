// ════════════════════════════════════════════════════════════════════
//  main.cpp  —  Domino controller
//  Teensy 4.0 / SMA Controller v1.6
//
//  [동작]
//    Serial로 '1'~'6' 수신 → 해당 도미노 솔레노이드만 실행
//      1) 솔레노이드 펄스 (MOSFET 채널 ON → 200ms → OFF) → 도미노 쓰러짐 → IDLE
//    서보(도미노 일으켜 세우기)는 자동으로 동작하지 않으며, 별도의 서보
//    raise-only 명령(XBee cmd 11~16 / 17)으로만 트리거된다.
//      - 서보 2500→1750 (1.5초 sweep) → 도미노 일으켜 세움
//      - 1초 유지
//      - 서보 1750→2500 (0.5초 sweep) → 서보 복귀 / IDLE
//    모든 트리거 명령은 busy 상태 체크 없이 즉시 실행되며, 진행 중인
//    시퀀스가 있어도 새 명령이 오면 그 즉시 덮어쓴다.
//
//  핀 배치 (SMA Controller v1.6)
//    MOSFET1 (솔레노이드 1) : pin 2
//    MOSFET2 (솔레노이드 2) : pin 3
//    MOSFET3 (솔레노이드 3) : pin 4
//    MOSFET4 (솔레노이드 4) : pin 5
//    MOSFET5 (솔레노이드 5) : pin 6
//    MOSFET6 (솔레노이드 6) : pin 7
//    PCA9685 Wire (I2C)    : SDA=18, SCL=19  addr=0x40
//      시리얼 '1' -> ch 0
//      시리얼 '2' -> ch 1
//      시리얼 '3' -> ch 2
//      시리얼 '4' -> ch 3
//      시리얼 '5' -> ch 4
//      시리얼 '6' -> ch 5,6
//
//  서보 기준값 (서보 테스터 측정)
//    2500 µs = 기본 상태 (서보 대기, 도미노 세워진 상태)
//    1750 µs = 도미노 일으켜 세운 상태
// ════════════════════════════════════════════════════════════════════

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

#define XBEE Serial1

static constexpr uint8_t START_B = 255;
static constexpr uint8_t END_B = 254;
static constexpr uint8_t DOMINO_FLAG = 0xD1;

// ── 핀 (MOSFET 채널 1~6) ────────────────────────────────────────────
static const uint8_t SOL_PINS[6] = { 2, 3, 4, 5, 6, 7 };

// ── PCA9685 설정 ─────────────────────────────────────────────────────
#define PCA9685_ADDR   0x40
#define SERVO_FREQ     50        // Hz

// ── 타이밍 파라미터 ──────────────────────────────────────────────────
#define SERVO_HOME     2650      // µs — 기본 대기 위치
#define SERVO_RAISED   1950      // µs — 도미노 일으켜 세운 위치
#define DOMINO6_AUX_CH_ENABLED 1 // 1: domino 6에서 ch6 동시 구동, 0: ch5만 사용
#define SOL_PULSE_MS    200      // 솔레노이드 펄스 시간 (ms)
#define SOL_STAGGER_MS   60      // 그룹 트리거 시 솔레노이드 시작 간격 (ms)
#define SWEEP_UP_MS    1500      // 서보 2500→1750 sweep 시간 (ms)
#define HOLD_MS        1000      // 도미노 세운 후 유지 시간 (ms)
#define SWEEP_DN_MS     500      // 서보 1750→2500 복귀 시간 (ms)

// ── I2C(PCA9685) 헬스체크 / 재연결 파라미터 ──────────────────────────
#define I2C_HEALTH_CHECK_MS  500  // 헬스체크 주기 (ms)
#define I2C_FAIL_THRESHOLD     3  // 연속 실패 횟수 → 연결 끊김 판정
#define I2C_RECONNECT_RETRY_MS 500 // 끊김 상태에서 재시도 간격 (ms)

// ── 상태 머신 ────────────────────────────────────────────────────────
enum DominoState : uint8_t {
  IDLE,
  SOL_QUEUED, // 솔레노이드 ON 대기 (전류 피크 분산)
  SOL_ON,     // 솔레노이드 ON 중
  RAISING,    // 서보 2500→1750 sweep
  HOLDING,    // 서보 1750 유지
  RETURNING   // 서보 1750→2500 sweep
};

struct Domino {
  DominoState state = IDLE;
  uint32_t    stateStart = 0;
};

static Domino dominos[6];
static Adafruit_PWMServoDriver pwm(PCA9685_ADDR);
static char serialCmdBuf[32];
static uint8_t serialCmdLen = 0;
static uint8_t xbeePacket[4];
static uint8_t xbeeIdx = 0;

// ── I2C(PCA9685) 연결 상태 ────────────────────────────────────────────
static bool     i2cConnected     = true;
static uint8_t  i2cFailCount     = 0;
static uint32_t lastI2CCheckMs   = 0;
// ── I2C/PCA9685 감시·복구 ─────────────────────────────────────────────
static bool pcaReady = false;
static uint32_t lastPcaHealthCheckMs = 0;
static uint32_t lastPcaRecoverTryMs = 0;
static uint16_t pcaRecoverCount = 0;

static constexpr uint16_t I2C_RECOVER_GUARD_MS = 500;

// ── 서보 쓰기 ────────────────────────────────────────────────────────
inline uint16_t clampServoUs(uint16_t us) {
  const uint16_t low  = (SERVO_HOME < SERVO_RAISED) ? SERVO_HOME : SERVO_RAISED;
  const uint16_t high = (SERVO_HOME > SERVO_RAISED) ? SERVO_HOME : SERVO_RAISED;
  return constrain(us, low, high);
}

inline void servoWriteRaw(uint8_t ch, uint16_t us) {
  pwm.writeMicroseconds(ch, clampServoUs(us));
}

bool pcaPing() {
  Wire.beginTransmission(PCA9685_ADDR);
  return Wire.endTransmission() == 0;
}

bool initPca9685() {
  pwm.begin();
  pwm.setOscillatorFrequency(27000000);
  pwm.setPWMFreq(SERVO_FREQ);
  delay(10);
  return pcaPing();
}

bool recoverPca9685(const char* reason) {
  const uint32_t now = millis();
  if (now - lastPcaRecoverTryMs < I2C_RECOVER_GUARD_MS) {
    return pcaReady;
  }
  lastPcaRecoverTryMs = now;

  Serial.printf("[I2C] PCA9685 recovery start (%s)\n", reason);

  // Teensy에서는 Wire.begin() 재호출로 버스 재초기화를 시도한다.
  Wire.begin();
  Wire.setClock(100000);
  pcaReady = initPca9685();

  if (pcaReady) {
    pcaRecoverCount++;
    Serial.printf("[I2C] PCA9685 recovery OK (count=%u)\n", pcaRecoverCount);
  } else {
    Serial.println("[I2C] PCA9685 recovery FAIL");
  }
  return pcaReady;
}

bool ensurePcaReady(const char* reason) {
  if (pcaReady) {
    return true;
  }
  return recoverPca9685(reason);
}

inline void servoWrite(uint8_t ch, uint16_t us) {
  if (!ensurePcaReady("servoWrite")) {
    return;
  }
  servoWriteRaw(ch, us);
}

inline void servoWriteDomino(uint8_t dominoIdx, uint16_t us) {
  servoWrite(dominoIdx, us);
  if (dominoIdx == 5 && DOMINO6_AUX_CH_ENABLED) {
    // 마지막 도미노(6번)만 보조 채널(ch6)도 함께 구동
    servoWrite(6, us);
  }
}

void resetAllDominos() {
  Serial.println("[CMD]  전체 서보 홈 리셋");
  for (int i = 0; i < 6; i++) {
    dominos[i].state = IDLE;
    digitalWrite(SOL_PINS[i], LOW);
    servoWriteDomino(i, SERVO_HOME);
  }
}

// ── I2C(PCA9685) 헬스체크 / 재연결 ────────────────────────────────────
// PCA9685에 실제 데이터를 쓰지 않고 주소만 호출해 응답 여부를 확인한다.
bool pca9685Ping() {
  Wire.beginTransmission(PCA9685_ADDR);
  return Wire.endTransmission() == 0;
}

// PCA9685 재초기화를 시도하고, 성공하면 모든 도미노를 안전한 홈 상태로 되돌린다.
bool attemptPCA9685Reconnect() {
  pwm.begin();
  pwm.setOscillatorFrequency(27000000);
  pwm.setPWMFreq(SERVO_FREQ);
  delay(5);

  if (!pca9685Ping()) return false;

  Serial.println("[INFO] PCA9685 재연결 성공 → 전체 서보 홈 리셋");
  resetAllDominos();
  return true;
}

// loop()에서 주기적으로 호출: I2C 연결 상태를 감시하고 끊기면 재연결을 시도한다.
void checkI2CHealth() {
  const uint32_t now = millis();
  const uint32_t interval = i2cConnected ? I2C_HEALTH_CHECK_MS : I2C_RECONNECT_RETRY_MS;
  if (now - lastI2CCheckMs < interval) return;
  lastI2CCheckMs = now;

  if (pca9685Ping()) {
    i2cFailCount = 0;
    i2cConnected = true;
    return;
  }

  if (i2cConnected) {
    i2cFailCount++;
    if (i2cFailCount >= I2C_FAIL_THRESHOLD) {
      i2cConnected = false;
      Serial.println("[ERROR] PCA9685 I2C 연결 끊김 감지 → 재연결 시도 시작");
    }
    return;
  }

  if (!attemptPCA9685Reconnect()) {
    Serial.println("[WARN] PCA9685 재연결 시도 실패 → 다음 주기에 재시도");
  }
}

void triggerDominoGroup(uint8_t mask) {
  if (mask == 0) return;

  uint32_t startAt = millis();
  uint8_t queuedCount = 0;
  for (uint8_t i = 0; i < 6; i++) {
    if ((mask & (1u << i)) == 0) continue;

    uint32_t delayMs = (uint32_t)queuedCount * SOL_STAGGER_MS;
    dominos[i].state = SOL_QUEUED;
    dominos[i].stateStart = startAt + delayMs;
    if (delayMs == 0) {
      Serial.printf("[CMD]  domino %d: solenoid queued (0 ms)\n", i + 1);
    } else {
      Serial.printf("[CMD]  domino %d: solenoid queued (+%lu ms)\n", i + 1, (unsigned long)delayMs);
    }
    queuedCount++;
  }
}

void triggerServoRaiseOnly(uint8_t idx) {
  if (idx >= 6) return;

  Domino& d = dominos[idx];
  if (d.state != IDLE) {
    // raise-only는 현재 시퀀스를 즉시 중단하고 서보 동작을 우선한다.
    digitalWrite(SOL_PINS[idx], LOW);
    Serial.printf("[CMD]  domino %d: force servo raise (override state=%u)\n", idx + 1, static_cast<unsigned>(d.state));
  } else {
    Serial.printf("[CMD]  domino %d: servo raise-only trigger\n", idx + 1);
  }

  d.state = RAISING;
  d.stateStart = millis();
}

void triggerAllServoRaiseOnly() {
  for (uint8_t i = 0; i < 6; i++) {
    triggerServoRaiseOnly(i);
  }
}

// ── 도미노 트리거 ─────────────────────────────────────────────────────
void triggerDomino(uint8_t idx) {
  if (idx >= 6) return;
  triggerDominoGroup((uint8_t)(1u << idx));
}

void processSerialCommandBuffer() {
  if (serialCmdLen == 0) return;

  uint8_t selectedMask = 0;
  bool resetRequested = false;

  for (uint8_t i = 0; i < serialCmdLen; i++) {
    char c = serialCmdBuf[i];
    if (c >= '1' && c <= '6') {
      selectedMask |= (uint8_t)(1u << (c - '1'));
    } else if (c == 'a' || c == 'A') {
      selectedMask = 0x3F;  // 6비트 모두 ON
    } else if (c == 'r' || c == 'R') {
      resetRequested = true;
    } else if (c == ' ' || c == ',' || c == '\t' || c == '\r' || c == '\n') {
      // 구분자 문자는 무시
    } else {
      Serial.printf("[WARN] unknown cmd: '%c'\n", c);
    }
  }

  if (resetRequested) {
    resetAllDominos();
  } else if (selectedMask != 0) {
    if (selectedMask == 0x3F) Serial.println("[CMD]  전체 도미노 트리거");
    triggerDominoGroup(selectedMask);
  }

  serialCmdLen = 0;
}

void processXBeeCommand(uint8_t cmd) {
  if (cmd >= 1 && cmd <= 6) {
    triggerDomino(static_cast<uint8_t>(cmd - 1));
    return;
  }

  if (cmd == 7) {
    Serial.println("[XBEE] all domino trigger");
    triggerDominoGroup(0x3F);
    return;
  }

  if (cmd >= 11 && cmd <= 16) {
    triggerServoRaiseOnly(static_cast<uint8_t>(cmd - 11));
    return;
  }

  if (cmd == 17) {
    Serial.println("[XBEE] all servo raise-only trigger");
    triggerAllServoRaiseOnly();
    return;
  }

  Serial.printf("[XBEE] unsupported cmd: %u\n", cmd);
}

void readXBeePacket() {
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
      Serial.println("[XBEE] bad frame");
      continue;
    }

    const uint8_t flag = xbeePacket[1];
    const uint8_t cmd = xbeePacket[2];

    if (flag != DOMINO_FLAG) {
      continue;
    }

    Serial.printf("[XBEE] flag=0x%02X cmd=%u\n", flag, cmd);
    processXBeeCommand(cmd);
  }
}

// ── 상태 머신 업데이트 (loop마다 호출) ───────────────────────────────
void updateDomino(uint8_t i) {
  Domino&  d       = dominos[i];
  uint32_t elapsed = millis() - d.stateStart;

  switch (d.state) {
    case IDLE: return;

    case SOL_QUEUED:
      if ((int32_t)(millis() - d.stateStart) >= 0) {
        digitalWrite(SOL_PINS[i], HIGH);
        Serial.printf("[INFO] domino %d: solenoid ON\n", i + 1);
        d.state = SOL_ON;
        d.stateStart = millis();
      }
      break;

    case SOL_ON:
      if (elapsed >= SOL_PULSE_MS) {
        digitalWrite(SOL_PINS[i], LOW);
        Serial.printf("[INFO] domino %d: solenoid OFF → IDLE (servo raise는 별도 명령 필요)\n", i + 1);
        d.state = IDLE;
      }
      break;

    case RAISING: {
      float    t  = constrain((float)elapsed / SWEEP_UP_MS, 0.0f, 1.0f);
      uint16_t us = (uint16_t)(SERVO_HOME + t * (int)(SERVO_RAISED - SERVO_HOME));
      servoWriteDomino(i, us);
      if (elapsed >= SWEEP_UP_MS) {
        servoWriteDomino(i, SERVO_RAISED);
        Serial.printf("[INFO] domino %d: holding at raised (%d µs)\n", i + 1, SERVO_RAISED);
        d.state = HOLDING; d.stateStart = millis();
      }
      break;
    }

    case HOLDING:
      if (elapsed >= HOLD_MS) {
        Serial.printf("[INFO] domino %d: returning (1750→2500 / %d ms)\n", i + 1, SWEEP_DN_MS);
        d.state = RETURNING; d.stateStart = millis();
      }
      break;

    case RETURNING: {
      float    t  = constrain((float)elapsed / SWEEP_DN_MS, 0.0f, 1.0f);
      uint16_t us = (uint16_t)(SERVO_RAISED + t * (int)(SERVO_HOME - SERVO_RAISED));
      servoWriteDomino(i, us);
      if (elapsed >= SWEEP_DN_MS) {
        servoWriteDomino(i, SERVO_HOME);
        Serial.printf("[INFO] domino %d: home → IDLE\n", i + 1);
        d.state = IDLE;
      }
      break;
    }
  }
}

void monitorAndRecoverI2C() {
  const uint32_t now = millis();
  if (now - lastPcaHealthCheckMs < I2C_HEALTH_CHECK_MS) return;
  lastPcaHealthCheckMs = now;

  if (!pcaPing()) {
    pcaReady = false;
    recoverPca9685("health-check");
  }
}

// ────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000);
  XBEE.begin(115200);
  Serial.println("=== Domino controller boot ===");

  // 솔레노이드 핀 초기화
  for (int i = 0; i < 6; i++) {
    pinMode(SOL_PINS[i], OUTPUT);
    digitalWrite(SOL_PINS[i], LOW);
  }

  // PCA9685 초기화
  Wire.begin();
  Wire.setClock(100000);
  pcaReady = initPca9685();
  if (!pcaReady) {
    Serial.println("[WARN] PCA9685 not responding at boot; auto-recovery enabled");
  }

  // 기본 서보 채널(0~5) + 마지막 도미노 보조 채널(6) 홈 위치로
  for (int i = 0; i < 6; i++) servoWriteDomino(i, SERVO_HOME);

  Serial.println("[READY] '1'~'6' : 해당 도미노 트리거");
  Serial.println("        숫자 조합: 선택 도미노 동시 트리거 (예: 135, 1,3,5)");
  Serial.println("        'a'     : 전체 동시 트리거");
  Serial.println("        'r'     : 전체 서보 홈 리셋");
  Serial.printf("[READY] XBee frame: [255][0x%02X][cmd][254], cmd=1~6 solenoid, 7 all solenoid, 11~16 servo, 17 all servo\n", DOMINO_FLAG);
}

void loop() {
  // 시리얼 커맨드: 줄바꿈(Enter) 기준으로만 처리해 타이핑 중 오작동 방지
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      processSerialCommandBuffer();
      continue;
    }

    if (serialCmdLen < sizeof(serialCmdBuf) - 1) {
      serialCmdBuf[serialCmdLen++] = c;
    } else {
      // 버퍼가 가득 차면 우선 처리 후 새 입력을 받는다.
      processSerialCommandBuffer();
      if (c != '\n' && c != '\r') {
        serialCmdBuf[serialCmdLen++] = c;
      }
    }
  }

  checkI2CHealth();

  readXBeePacket();
  monitorAndRecoverI2C();

  // 6개 도미노 상태 머신 업데이트
  for (int i = 0; i < 6; i++) updateDomino(i);
}
