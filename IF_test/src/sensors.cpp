// sensors.cpp  —  MLX90614 온도 센서 관리
#include "sensors.h"

static Adafruit_MLX90614 mlx1;
static Adafruit_MLX90614 mlx2;

bool    mlx1Ok = false, mlx2Ok = false;
float   objTemp1 = 25.0f, objTemp2 = 25.0f, ambTemp = 25.0f, ambTemp2 = 25.0f;
bool    sensorError    = false;
uint8_t sensorErrorId  = 0;
uint8_t sensorFaultCode = FAULT_NONE;

/* ── 이전 복잡한 센서 안전장치 (주석처리)
static const float SPIKE_THRESH = 15.0f;
static float last1 = -999.0f, last2 = -999.0f;
static uint8_t spike1Cnt = 0, spike2Cnt = 0;
static const float EMA2_ALPHA    = 0.2f;
static const float EMA2_CLAMP_UP = 2.0f;
static float ema2 = -999.0f;
static uint8_t mlx1FailCnt = 0, mlx2FailCnt = 0;
static const uint8_t MLX1_FAIL_MAX = 3;
static const uint8_t MLX2_FAIL_MAX = 3;
static uint8_t sameRecovery1 = 0, sameRecovery2 = 0;
static const uint8_t SAME_RECOVERY_MAX = 5;
─── */

// ── 유효 범위: 5~85°C (실내 SMA 설치 기준, 5°C 미만 = 오염값)
static const float TEMP_MIN = 5.0f;
static const float TEMP_MAX = 85.0f;

// ── 범위 이탈 연속 카운터: 10회 연속 → 센서 에러 (일시적 I2C 글리치 완충)
static uint8_t failCount1 = 0, failCount2 = 0;
static const uint8_t FAIL_MAX = 10;

// ── 20초 동일값 감지: 100ms × 200 = 20초 연속 동일 → 센서 에러
static const uint16_t SAME_LIMIT = 200;
static const float   SAME_THR   = 0.015f;  // 1 LSB(0.02°C) 미만 → 1 LSB 변화도 '다름'으로 처리, 실온 안정 시 오진 방지
static uint16_t sameCount1 = 0, sameCount2 = 0;
static float   lastRaw1   = -999.0f, lastRaw2 = -999.0f;

// ── 부팅 직후 워밍업: 센서/전원 안정화 시간 동안 실패 카운트 누적 제외
static const uint32_t SENSOR_WARMUP_MS = 10000UL;

/* ── I2C 버스 언락 (주석처리)
static void i2cBusRecover(TwoWire &wire, uint8_t sdaPin, uint8_t sclPin) {
  wire.end();
  pinMode(sclPin, OUTPUT); pinMode(sdaPin, INPUT_PULLUP);
  for (uint8_t i = 0; i < 9; i++) {
    digitalWrite(sclPin, HIGH); delayMicroseconds(5);
    digitalWrite(sclPin, LOW);  delayMicroseconds(5);
  }
  pinMode(sdaPin, OUTPUT); digitalWrite(sdaPin, LOW);
  delayMicroseconds(5);
  digitalWrite(sclPin, HIGH); delayMicroseconds(5);
  digitalWrite(sdaPin, HIGH); delayMicroseconds(5);
  wire.begin(); wire.setClock(25000);
}
─── */

// Wire 직접 읽기 — Adafruit 라이브러리 write_then_read 동일 버퍼 문제 우회
static float readRegDirect(TwoWire &wire, uint8_t addr, uint8_t reg) {
  wire.beginTransmission(addr);
  wire.write(reg);
  if (wire.endTransmission(false) != 0) return NAN;
  if (wire.requestFrom(addr, (uint8_t)3) != 3) return NAN;
  uint8_t lo = wire.read();
  uint8_t hi = wire.read();
  wire.read();  // PEC discard
  uint16_t raw = (uint16_t)lo | ((uint16_t)hi << 8);
  if (raw == 0 || (raw & 0x8000)) return NAN;
  return raw * 0.02f - 273.15f;
}

static float readObjectDirect(TwoWire &wire, uint8_t addr) {
  return readRegDirect(wire, addr, 0x07);  // MLX90614_TOBJ1
}

static float readAmbientDirect(TwoWire &wire, uint8_t addr) {
  return readRegDirect(wire, addr, 0x06);  // MLX90614_TA
}

static void recoverI2cBus(TwoWire &wire, uint8_t sdaPin, uint8_t sclPin, uint32_t hz) {
  wire.end();
  pinMode(sclPin, OUTPUT);
  pinMode(sdaPin, INPUT_PULLUP);
  for (uint8_t i = 0; i < 9; i++) {
    digitalWrite(sclPin, HIGH); delayMicroseconds(5);
    digitalWrite(sclPin, LOW);  delayMicroseconds(5);
  }
  // STOP condition to release a stuck slave state machine.
  pinMode(sdaPin, OUTPUT); digitalWrite(sdaPin, LOW); delayMicroseconds(5);
  digitalWrite(sclPin, HIGH); delayMicroseconds(5);
  digitalWrite(sdaPin, HIGH); delayMicroseconds(5);
  wire.begin();
  wire.setClock(hz);
}

void sensorsInit() {
  // 이전 에러 상태를 먼저 지운 뒤 재초기화 시도
  mlx1Ok = false;
  mlx2Ok = false;
  sensorError = false;
  sensorErrorId = 0;
  sensorFaultCode = FAULT_NONE;

  failCount1 = failCount2 = 0;
  sameCount1 = sameCount2 = 0;
  lastRaw1 = lastRaw2 = -999.0f;

  // MLX90614 전원/EEPROM 안정화 대기
  delay(500);

  // 일부 개체에서 콜드부트 직후 두 버스가 동시에 초기화 실패하므로
  // 버스 복구 + begin()을 여러 라운드로 재시도한다.
  for (uint8_t round = 0; round < 5 && (!mlx1Ok || !mlx2Ok); round++) {
    recoverI2cBus(Wire, 18, 19, 100000);
    recoverI2cBus(Wire2, 24, 25, 50000);

    if (!mlx1Ok) mlx1Ok = mlx1.begin(0x5A, &Wire);
    if (!mlx2Ok) mlx2Ok = mlx2.begin(0x5A, &Wire2);

    // begin() 성공 후에도 첫 읽기가 NAN이면 미안정 상태로 보고 다음 라운드 재시도.
    if (mlx1Ok) {
      float t1 = readObjectDirect(Wire, 0x5A);
      if (isnan(t1)) mlx1Ok = false;
    }
    if (mlx2Ok) {
      float t2 = readObjectDirect(Wire2, 0x5A);
      if (isnan(t2)) mlx2Ok = false;
    }

    if (!mlx1Ok || !mlx2Ok) {
      Serial.print("[MLX INIT] retry round "); Serial.println(round + 1);
      delay(120 + round * 60);
    }
  }

  Serial.print("[MLX1] "); Serial.println(mlx1Ok ? "OK" : "NOT FOUND");
  Serial.print("[MLX2] "); Serial.println(mlx2Ok ? "OK" : "NOT FOUND");

  /* ── EMA2 초기화 (주석처리)
  if (mlx2Ok) {
    delay(100);
    float o = readObjectDirect(Wire2, 0x5A);
    if (!isnan(o) && o > 5.0f && o < 50.0f) { ema2 = o; last2 = o; }
    else { ema2 = 25.0f; last2 = 25.0f; }
  }
  ─── */

  if (!mlx1Ok || !mlx2Ok) {
    sensorError = true;
    if (!mlx1Ok && !mlx2Ok) { sensorErrorId = 3; sensorFaultCode = FAULT_BOTH_INIT; }
    else if (!mlx1Ok)        { sensorErrorId = 1; sensorFaultCode = FAULT_MLX1_INIT; }
    else                     { sensorErrorId = 2; sensorFaultCode = FAULT_MLX2_INIT; }
    Serial.print("[SENSOR ERR] init fail id="); Serial.println(sensorErrorId);
  }
}

void sensorsResetEma2() {
  // EMA 제거됨 — no-op (호환성 유지)
}

void readTemps(State currentState) {
  (void)currentState;  // 상태별 EMA 로직 제거됨
  if (sensorError) return;

  // 100ms 레이트 리미터
  static unsigned long lastRead = 0;
  unsigned long nowMs = millis();
  if (nowMs - lastRead < 100) return;
  lastRead = nowMs;

  const bool inWarmup = (nowMs < SENSOR_WARMUP_MS);
  const bool enableFrozenDetect = (!inWarmup && currentState != IDLE);

  // ── MLX1
  if (mlx1Ok) {
    float o = readObjectDirect(Wire, 0x5A);
    if (!isnan(o) && o > TEMP_MIN && o < TEMP_MAX) {
      failCount1 = 0;
      if (enableFrozenDetect) {
        // 장시간 동일값 감지 (IDLE/워밍업 구간 제외)
        if (lastRaw1 > -900.0f && fabsf(o - lastRaw1) < SAME_THR) {
          if (++sameCount1 >= SAME_LIMIT) {
            sensorError = true; sensorErrorId = 1; sensorFaultCode = FAULT_MLX1_FROZEN;
            Serial.println("[MLX1 ERR] 20초 동일값 → sensor error");
            return;
          }
        } else { sameCount1 = 0; }
      } else {
        sameCount1 = 0;
      }
      lastRaw1 = o;
      objTemp1 = o;
    } else {
      if (!inWarmup && ++failCount1 >= FAIL_MAX) {
        sensorError = true; sensorErrorId = 1; sensorFaultCode = FAULT_MLX1_FAIL;
        Serial.print("[MLX1 ERR] out of range val="); Serial.print(o, 2);
        Serial.println(" → sensor error");
      }
    }
  }

  // ── MLX2
  if (mlx2Ok) {
    float o = readObjectDirect(Wire2, 0x5A);
    if (!isnan(o) && o > TEMP_MIN && o < TEMP_MAX) {
      failCount2 = 0;
      if (enableFrozenDetect) {
        // 장시간 동일값 감지 (IDLE/워밍업 구간 제외)
        if (lastRaw2 > -900.0f && fabsf(o - lastRaw2) < SAME_THR) {
          if (++sameCount2 >= SAME_LIMIT) {
            sensorError = true; sensorErrorId = 2; sensorFaultCode = FAULT_MLX2_FROZEN;
            Serial.println("[MLX2 ERR] 20초 동일값 → sensor error");
            return;
          }
        } else { sameCount2 = 0; }
      } else {
        sameCount2 = 0;
      }
      lastRaw2 = o;
      objTemp2 = o;
    } else {
      if (!inWarmup && ++failCount2 >= FAIL_MAX) {
        sensorError = true; sensorErrorId = 2; sensorFaultCode = FAULT_MLX2_FAIL;
        Serial.print("[MLX2 ERR] out of range val="); Serial.print(o, 2);
        Serial.println(" → sensor error");
      }
    }
  }

  // ── Ambient
  if (mlx1Ok) {
    float a = readAmbientDirect(Wire, 0x5A);
    if (!isnan(a) && a > 0.0f && a < 80.0f) ambTemp = a;
  }
  if (mlx2Ok) {
    float a = readAmbientDirect(Wire2, 0x5A);
    if (!isnan(a) && a > 0.0f && a < 80.0f) ambTemp2 = a;
  }
}

// ── Wire2 버스 강제 해제 ─────────────────────────────────────────────
// MLX2 에러로 SDA 가 LOW 에 묶인 경우 bit-bang 으로 버스 해제.
// SENSOR_ERROR 진입 시 Wire2 복구용으로 호출.
void wire2FreeBus() {
  Wire2.end();
  pinMode(25, OUTPUT); pinMode(24, INPUT_PULLUP);
  for (uint8_t i = 0; i < 9; i++) {
    digitalWrite(25, HIGH); delayMicroseconds(5);
    digitalWrite(25, LOW);  delayMicroseconds(5);
  }
  // STOP 조건
  pinMode(24, OUTPUT); digitalWrite(24, LOW); delayMicroseconds(5);
  digitalWrite(25, HIGH); delayMicroseconds(5);
  digitalWrite(24, HIGH); delayMicroseconds(5);
  Wire2.begin(); Wire2.setClock(50000);
  Serial.println("[Wire2] 버스 강제 해제 완료");
}

// ── SENSOR_ERROR 자동 복구 ───────────────────────────────────────────
// 호출 조건: state == SENSOR_ERROR, 10초 경과 후 main.cpp 에서 호출
// INIT 계열(F13/F23/F31) 포함해 재초기화 시도
// 복구 성공 시 sensorError=false, sensorFaultCode=FAULT_NONE 으로 클리어
bool sensorsAutoRecover() {
  if (!sensorError) return true;

  // MLX2 계열 (F21/F22/F23/F24/F31)
  if (sensorErrorId == 2 || sensorErrorId == 3) {
    recoverI2cBus(Wire2, 24, 25, 50000);
    mlx2Ok = mlx2.begin(0x5A, &Wire2);
    if (mlx2Ok && isnan(readObjectDirect(Wire2, 0x5A))) mlx2Ok = false;
    failCount2 = 0; sameCount2 = 0; lastRaw2 = -999.0f;
    Serial.print("[AUTO-RECOVER] MLX2 재초기화 → "); Serial.println(mlx2Ok ? "OK" : "FAIL");
  }

  // MLX1 계열 (F11/F12/F13/F31)
  if (sensorErrorId == 1 || sensorErrorId == 3) {
    recoverI2cBus(Wire, 18, 19, 100000);
    for (uint8_t i = 0; i < 3 && !mlx1Ok; i++) {
      mlx1Ok = mlx1.begin(0x5A, &Wire);
      if (mlx1Ok && isnan(readObjectDirect(Wire, 0x5A))) mlx1Ok = false;
      if (!mlx1Ok) delay(80);
    }
    failCount1 = 0; sameCount1 = 0; lastRaw1 = -999.0f;
    Serial.print("[AUTO-RECOVER] MLX1 재초기화 → "); Serial.println(mlx1Ok ? "OK" : "FAIL");
  }

  bool ok;
  if (sensorErrorId == 3)      ok = mlx1Ok && mlx2Ok;
  else if (sensorErrorId == 1) ok = mlx1Ok;
  else                         ok = mlx2Ok;
  if (ok) {
    sensorError     = false;
    sensorFaultCode = FAULT_NONE;
    sensorErrorId   = 0;
  }
  return ok;
}
