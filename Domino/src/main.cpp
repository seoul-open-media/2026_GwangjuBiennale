// ════════════════════════════════════════════════════════════════════
//  main.cpp  —  Domino controller
//  Teensy 4.0 / SMA Controller v1.6
//
//  [동작]
//    Serial로 '1'~'6' 수신 → 해당 도미노 시퀀스 실행
//      1) 솔레노이드 펄스 (MOSFET 채널 ON → 200ms → OFF) → 도미노 쓰러짐
//      2) 3초 대기
//      3) 서보 2500→1750 (1초 sweep) → 도미노 일으켜 세움
//      4) 500ms 유지
//      5) 서보 1750→2500 (1초 sweep) → 서보 복귀 / IDLE
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

// ── 핀 (MOSFET 채널 1~6) ────────────────────────────────────────────
static const uint8_t SOL_PINS[6] = { 2, 3, 4, 5, 6, 7 };

// ── PCA9685 설정 ─────────────────────────────────────────────────────
#define PCA9685_ADDR   0x40
#define SERVO_FREQ     50        // Hz

// ── 타이밍 파라미터 ──────────────────────────────────────────────────
#define SERVO_HOME     2650      // µs — 기본 대기 위치
#define SERVO_RAISED   1950      // µs — 도미노 일으켜 세운 위치
#define SOL_PULSE_MS    200      // 솔레노이드 펄스 시간 (ms)
#define SOL_STAGGER_MS   60      // 그룹 트리거 시 솔레노이드 시작 간격 (ms)
#define WAIT_MS        5000      // 솔레노이드 OFF 후 서보 동작까지 대기 (ms)
#define SWEEP_UP_MS    1000      // 서보 2500→1750 sweep 시간 (ms)
#define HOLD_MS         500      // 도미노 세운 후 유지 시간 (ms)
#define SWEEP_DN_MS    1000      // 서보 1750→2500 복귀 시간 (ms)

// ── 상태 머신 ────────────────────────────────────────────────────────
enum DominoState : uint8_t {
  IDLE,
  SOL_QUEUED, // 솔레노이드 ON 대기 (전류 피크 분산)
  SOL_ON,     // 솔레노이드 ON 중
  WAITING,    // 솔레노이드 OFF, 서보 동작 전 대기
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

// ── 서보 쓰기 ────────────────────────────────────────────────────────
inline void servoWrite(uint8_t ch, uint16_t us) {
  pwm.writeMicroseconds(ch, us);
}

inline void servoWriteDomino(uint8_t dominoIdx, uint16_t us) {
  servoWrite(dominoIdx, us);
  if (dominoIdx == 5) {
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

void triggerDominoGroup(uint8_t mask) {
  if (mask == 0) return;

  uint32_t startAt = millis();
  uint8_t queuedCount = 0;
  for (uint8_t i = 0; i < 6; i++) {
    if ((mask & (1u << i)) == 0) continue;
    if (dominos[i].state != IDLE) {
      Serial.printf("[WARN] domino %d already running — ignored\n", i + 1);
      continue;
    }

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

// ── 도미노 트리거 ─────────────────────────────────────────────────────
void triggerDomino(uint8_t idx) {
  if (idx >= 6) return;
  triggerDominoGroup((uint8_t)(1u << idx));
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
        Serial.printf("[INFO] domino %d: solenoid OFF → waiting %d ms\n", i + 1, WAIT_MS);
        d.state = WAITING; d.stateStart = millis();
      }
      break;

    case WAITING:
      if (elapsed >= WAIT_MS) {
        Serial.printf("[INFO] domino %d: raising (2500→1750 / %d ms)\n", i + 1, SWEEP_UP_MS);
        d.state = RAISING; d.stateStart = millis();
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

// ────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000);
  Serial.println("=== Domino controller boot ===");

  // 솔레노이드 핀 초기화
  for (int i = 0; i < 6; i++) {
    pinMode(SOL_PINS[i], OUTPUT);
    digitalWrite(SOL_PINS[i], LOW);
  }

  // PCA9685 초기화
  Wire.begin();
  pwm.begin();
  pwm.setOscillatorFrequency(27000000);
  pwm.setPWMFreq(SERVO_FREQ);
  delay(10);

  // 기본 서보 채널(0~5) + 마지막 도미노 보조 채널(6) 홈 위치로
  for (int i = 0; i < 6; i++) servoWriteDomino(i, SERVO_HOME);

  Serial.println("[READY] '1'~'6' : 해당 도미노 트리거");
  Serial.println("        숫자 조합: 선택 도미노 동시 트리거 (예: 135, 1,3,5)");
  Serial.println("        'a'     : 전체 동시 트리거");
  Serial.println("        'r'     : 전체 서보 홈 리셋");
}

void loop() {
  // 시리얼 커맨드
  if (Serial.available() > 0) {
    uint8_t selectedMask = 0;
    bool resetRequested = false;

    // 한 번에 들어온 시리얼 버스트를 모아 동시에 트리거한다.
    while (Serial.available() > 0) {
      char c = Serial.read();

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
  }

  // 6개 도미노 상태 머신 업데이트
  for (int i = 0; i < 6; i++) updateDomino(i);
}
