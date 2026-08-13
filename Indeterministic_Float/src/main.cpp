// ════════════════════════════════════════════════════════════════════
//  main.cpp  —  setup / loop
//  3-Axis Tension Equilibrium for Indeterministic Floating
//  Teensy 4.x  |  robot_id: 1~30  (★ config.h 의 MY_ROBOT_ID 참조)
// ════════════════════════════════════════════════════════════════════
#include <Arduino.h>
#include <Wire.h>
#include <Watchdog_t4.h>
#include "config.h"
#include "output.h"
#include "sensors.h"
#include "xbee.h"

// ── 워치독 타이머 (WDOG1, WDT_T4 라이브러리)
static WDT_T4<WDT1> wdt;

static void wdtInit() {
    WDT_timings_t cfg;
    cfg.timeout = 4.0;   // 4초 이내 kick 없으면 하드웨어 리셋
    cfg.trigger = 0.0;   // 인터럽트 없이 바로 리셋
    wdt.begin(cfg);
}

static void wdtKick() {
    wdt.feed();
}

State         state    = IDLE;
unsigned long stateMs  = 0;
unsigned long lastSensor = 0;  // 센서 읽기 주기 (별도 관리로 OLED Wire2와 충돌 방지)
unsigned long lastStatus  = 0;
unsigned long nextStatus  = 0;  // 랜덤 지터 적용 다음 전송 시각
uint8_t       swState    = 0;    // 현재 스위치 상태
unsigned long swMs       = 0;    // 스위치 마지막 변경 시각
bool          swEverSeen = false; // 스위치가 한 번이라도 감지된 적 있는지
unsigned long coolAmbMs   = 0;        // 저점 도달 감지 시각 (0=미도달)
static const uint32_t COOL_EXTRA_MS = 0UL; // 저점 도달 즉시 재가열/정지 전환

// ── 프리셋 활성 상태 ────────────────────────────────────────────
uint8_t       presetActive    = 0;      // 현재 실행 중인 프리셋 (0=없음)
static const uint8_t SEQ_PRESETS[] = {2, 3, 4};  // 순환할 프리셋 목록
static const uint8_t SEQ_COUNT     = 3;
uint8_t       seqIdx           = 0;
uint8_t       seqCurrentPreset = 2;
unsigned long seqPresetStartMs = 0;
#define SEQ_PRESET_DURATION_MS 120000UL  // 프리셋당 재생 시간 (ms, 2분)

// ── 상태 전환 ────────────────────────────────────────────────────────
void enterState(State s) {
  state   = s;
  stateMs = millis();
  switch (s) {
    case IDLE:         allOff();            break;
    case HEATING:      smaOn(); sensorsResetEma2(); break;
    case SUSTAINING:   smaOff();            break;
    case COOLING:      fanOn(cmd.fanSpeed); coolAmbMs = 0; break;
    case SENSOR_ERROR: allOff();            break;
  }
  (void)(s == IDLE ? "IDLE" : "");  // state change — no log
}

// ── 사운드 커맨드 ─────────────────────────────────────────────────────
// 0:stop  1:sound1 once  2:sound1 loop  3:sound2 once  4:sound2 loop
void sendSound(uint8_t s) {
  AUDIO_SER.write(s);
}
// ── 프리셋 실행 (6/7번 순차·랜덤에서도 내부 호출) ──────────────────
void runPreset(uint8_t n) {
  unsigned long t = millis();
  switch (n) {
    case 1:
      Serial.println("[PRESET 1] 정지");
      presetActive = 0;
      allOff(); state = IDLE; stateMs = t;
      cmd.loopOn = false;
      break;
    case 2:
      Serial.println("[PRESET 2] 유영");
      presetActive = 0;
      cmd.tempTarget=40; cmd.sustainSec=15; cmd.fanSpeed=150;
      cmd.fanOnly=false; cmd.targetTempMin=0; cmd.heatPwm=0; cmd.loopOn=true;
      enterState(HEATING);
      break;
    case 3:
      Serial.println("[PRESET 3] 느린 유영");
      presetActive = 0;
      cmd.tempTarget=36; cmd.sustainSec=40; cmd.fanSpeed=100;
      cmd.fanOnly=false; cmd.targetTempMin=0; cmd.heatPwm=0; cmd.loopOn=true;
      enterState(HEATING);
      break;
    case 4:
      Serial.println("[PRESET 4] 힘찬 유영");
      presetActive = 0;
      cmd.tempTarget=43; cmd.sustainSec=5; cmd.fanSpeed=255;
      cmd.fanOnly=false; cmd.targetTempMin=0; cmd.heatPwm=0; cmd.loopOn=true;
      enterState(HEATING);
      break;
    case 5:
      Serial.println("[PRESET 5] 숨고르기");
      presetActive = 0;
      cmd.tempTarget=38; cmd.sustainSec=30; cmd.fanSpeed=120;
      cmd.fanOnly=false; cmd.targetTempMin=26; cmd.heatPwm=0; cmd.loopOn=true;
      enterState(HEATING);
      break;
    default: break;
  }
}
// ════════════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 2000) {}

#if defined(CORE_TEENSY)
  Wire.setSDA(18);  Wire.setSCL(19);
  Wire2.setSDA(24); Wire2.setSCL(25);
#endif
  Wire.begin();  Wire.setClock(100000);
  Wire2.begin(); Wire2.setClock(50000);
  delay(50);

  outputInit();
  xbeeInit();
  sensorsInit();
  pinMode(SW_PIN, INPUT_PULLUP);
  swState = !digitalRead(SW_PIN);
  if (swState) { swEverSeen = true; swMs = millis(); }

  enterState(IDLE);

  // ── 워치독 타임아웃 리셋 감지 (SRC_SRSR: 시스템 리셋 원인 레지스터)
  // sensorsInit() 이후에 덮어써야 센서 정상이어도 WDT 리셋을 보고할 수 있음
  if (SRC_SRSR & SRC_SRSR_WDOG_RST_B) {
    SRC_SRSR = SRC_SRSR_WDOG_RST_B;  // W1C: 클리어 (다음 리셋과 혼동 방지)
    sensorFaultCode = FAULT_WDT_FREEZE;
    sensorError     = true;
    Serial.println("[WDT] 워치독 타임아웃 리셋 감지");
  }

  wdtInit();

}

// ════════════════════════════════════════════════════════════════════
void loop() {
  wdtKick();  // 워치독 타임아웃 방지 — 여기를 지나지 못하면 3초 후 리셋
  unsigned long now = millis();

  readXBee();

  // 센서 100ms 주기 읽기 — MLX90614 내부 갱신 주기(~10Hz)에 맞춤
  // 더 빠르게 읽으면 레지스터 포인터가 꼬여 ambient(0x06)가 반환됨
  if (now - lastSensor >= 100) {
    lastSensor = now;
    readTemps(state);
  }

  // ── 센서 오류 감지 → 즉시 전체 정지 ─────────────────────────────
  if (sensorError && state != SENSOR_ERROR) {
    allOff();
    state   = SENSOR_ERROR;
    stateMs = now;
  }
  if (state == SENSOR_ERROR) {
    // SENSOR_ERROR 상태에서도 XBee 상태 패킷 송신 (PD/대시보드 에러 감지용)
    if (now - lastStatus >= 500) {
      lastStatus = now;
      nextStatus = now + 450 + random(100);
      sendStatus(state, now - stateMs, sensorError, sensorFaultCode,
                 objTemp1, objTemp2, swState,
                 swEverSeen ? (now - swMs) : 0);
    }
    // ── 자동 복구: 10초 후 센서 재초기화 시도 (최대 5회, INIT 계열 제외) ─
    static uint8_t autoRecoverCnt = 0;
    if (autoRecoverCnt < 5 && now - stateMs >= 10000UL) {
      ++autoRecoverCnt;
      Serial.print("[AUTO-RECOVER] 시도 #"); Serial.println(autoRecoverCnt);
      if (sensorsAutoRecover()) {
        Serial.println("[AUTO-RECOVER] 성공 → IDLE 복귀");
        // autoRecoverCnt 리셋 안 함 — 5회 소진 후 에러 영구 유지
        enterState(IDLE);
        return;
      }
      stateMs = now;  // 실패 시 10초 타이머 재시작
    }
    // RESET 커맨드(fanSpeed=0 && tempTarget=0)는 SENSOR_ERROR에서도 처리
    // — 센서 오류 클리어 후 IDLE 복귀, 다음 루프에서 센서 재시도
    if (newCmd && cmd.fanSpeed == 0 && cmd.tempTarget == 0) {
      newCmd          = false;
      sensorError     = false;
      sensorFaultCode = FAULT_NONE;
      autoRecoverCnt  = 0;
      enterState(IDLE);
      return;
    }
    return;
  }

  // ── 새 커맨드 처리 ───────────────────────────────────────────────
  if (newCmd) {
    newCmd = false;
    setSmaPwm(cmd.heatPwm);
    if (cmd.sound > 0) sendSound(cmd.sound);
    // ── 프리셋 모드 (mode=2) ──────────────────────────────────
    if (cmd.mode == 2) {
      uint8_t pn = cmd.presetNum;
      if (pn >= 1 && pn <= 5) {
        runPreset(pn);
      } else if (pn == 6) {
        Serial.println("[PRESET 6] 순차 재생 (2→3→4→…)");
        seqIdx = 0; seqCurrentPreset = SEQ_PRESETS[0];
        seqPresetStartMs = millis();
        runPreset(seqCurrentPreset);
        presetActive = 6;
      } else if (pn == 7) {
        Serial.println("[PRESET 7] 랜덤 재생");
        seqIdx = random(0, SEQ_COUNT);
        seqCurrentPreset = SEQ_PRESETS[seqIdx];
        seqPresetStartMs = millis();
        runPreset(seqCurrentPreset);
        presetActive = 7;
      } else {
        Serial.print("[PRESET] unknown: "); Serial.println(pn);
      }
      return;
    }
    if (cmd.fanOnly) {
      if (cmd.fanSpeed > 0) {
        allOff();
        fanOn(cmd.fanSpeed);
        state   = COOLING;
        stateMs = millis();
      } else {
        allOff();
        state   = IDLE;
        stateMs = millis();
      }
    } else if (cmd.fanSpeed == 0 && cmd.tempTarget == 0) {
      allOff();
      state   = IDLE;
      stateMs = millis();
    } else if (cmd.fanSpeed == 0 && state == COOLING) {
      allOff();
      state   = IDLE;
      stateMs = millis();
    } else if ((state == HEATING || state == SUSTAINING) &&
               cmd.tempTarget < (uint8_t)max(objTemp1, objTemp2)) {
      enterState(COOLING);
    } else {
      enterState(HEATING);
    }
  }

  // ── 상태 머신 ────────────────────────────────────────────────────
  switch (state) {

    case IDLE:
      break;

    case HEATING: {
      // ⚠ SMA ON / FAN 절대 OFF

      // ★ 절대 상한 / 시간 제한 안전장치
      if (objTemp1 >= HEAT_MAX_TEMP || objTemp2 >= HEAT_MAX_TEMP) {
        Serial.print("[HEAT] OVER-TEMP "); Serial.print(max(objTemp1,objTemp2),1); Serial.println("C → stop");
        smaOff();
        if (cmd.fanSpeed > 0) enterState(COOLING);
        else                  enterState(IDLE);
        break;
      }
      if (now - stateMs >= HEAT_MAX_MS) {
        Serial.println("[HEAT] TIMEOUT 1.5min → stop");
        smaOff();
        if (cmd.fanSpeed > 0) enterState(COOLING);
        else                  enterState(IDLE);
        break;
      }

      bool t1ok = objTemp1 >= (float)cmd.tempTarget;
      bool t2ok = objTemp2 >= (float)cmd.tempTarget;

      // 두 센서 모두 목표에 도달해야 다음 상태로
      if (t1ok && t2ok) {
        smaOff();
        if (cmd.sustainSec > 0)    enterState(SUSTAINING);
        else if (cmd.fanSpeed > 0) enterState(COOLING);
        else if (cmd.loopOn)       enterState(HEATING);
        else                       enterState(IDLE);
        break;
      }

      // 가열 시작 직후에는 양 그룹을 동시에 가열해 초기 한쪽 OFF 고착을 완화
      float tgt  = (float)cmd.tempTarget;
      if (now - stateMs < HEAT_BALANCE_GRACE_MS) {
        if (!smaActiveA && objTemp1 < tgt) smaOnA();
        if (!smaActiveB && objTemp2 < tgt) smaOnB();
        break;
      }

      // 균형 제어: 두 센서 차이 SMA_BALANCE_THRESH(2°C) 이내 유지
      // 앞선 그룹만 OFF → 뒤처진 그룹이 따라올 때까지 대기
      float diff = objTemp1 - objTemp2;
      if (diff > SMA_BALANCE_THRESH) {
        // Group A(ch1,2)가 앞섬 → A OFF, B 계속 (단, 센서2가 목표 미달일 때만 ON)
        if (smaActiveA)                            smaOffA();
        if (!smaActiveB && objTemp2 < tgt)         smaOnB();
      } else if (diff < -SMA_BALANCE_THRESH) {
        // Group B(ch3,4)가 앞섬 → B OFF, A 계속 (단, 센서1이 목표 미달일 때만 ON)
        if (smaActiveB)                            smaOffB();
        if (!smaActiveA && objTemp1 < tgt)         smaOnA();
      } else {
        // 허용 범위 내 → 목표 미달인 그룹만 ON (목표 달성 그룹은 재켜지지 않음)
        if (!smaActiveA && objTemp1 < tgt)         smaOnA();
        if (!smaActiveB && objTemp2 < tgt)         smaOnB();
      }
      break;
    }

    case SUSTAINING: {
      // ⚠ FAN 절대 OFF / 온도 유지를 위한 SMA 재가열 허용

      // ★ 절대 상한 안전장치
      if (objTemp1 >= HEAT_MAX_TEMP || objTemp2 >= HEAT_MAX_TEMP) {
        smaOff();
        if (cmd.fanSpeed > 0) enterState(COOLING);
        else                  enterState(IDLE);
        break;
      }

      // ★ SUSTAINING 전체 시간 상한 (sustainSec > HEAT_MAX_MS 명령 오더라도 1분 초과 불가)
      if (now - stateMs >= HEAT_MAX_MS) {
        Serial.println("[SUSTAIN] TIMEOUT 1min → stop");
        smaOff();
        if (cmd.fanSpeed > 0) enterState(COOLING);
        else if (cmd.loopOn)  enterState(HEATING);
        else                  enterState(IDLE);
        break;
      }

      // 두 센서 중 낮은 쪽 기준으로 재가열 (maxTemp 쓰면 한쪽이 높아 재가열 안 됨)
      unsigned long elapsed = (now - stateMs) / 1000UL;
      float minTemp = min(objTemp1, objTemp2);
      if (minTemp < (float)cmd.tempTarget - 2.0f) {
        if (!smaActive) smaOn();
      } else {
        if (smaActive) smaOff();
      }
      if (elapsed >= (unsigned long)cmd.sustainSec) {
        smaOff();
        if (cmd.fanSpeed > 0)  enterState(COOLING);
        else if (cmd.loopOn)   enterState(HEATING);
        else                   enterState(IDLE);
      }
      break;
    }

    case COOLING: {
      // 저점온도 기준: targetTempMin이 설정되어 있으면 그 값, 아니면 ambTemp+0.5
      float coolTarget = (cmd.targetTempMin >= 10 && cmd.targetTempMin <= 30)
                         ? (float)cmd.targetTempMin
                         : ambTemp + 0.5f;
      bool atTarget = (objTemp1 <= coolTarget && objTemp2 <= coolTarget);

      if (atTarget && coolAmbMs == 0) {
        coolAmbMs = millis();
      }

      // 냉각 최대 시간 초과 → IDLE (에러 없이 정상 복귀)
      unsigned long coolElapsed = now - stateMs;
      if (coolElapsed >= COOL_MAX_MS) {
        coolAmbMs = 0;
        fanOff();
        Serial.print("[COOL TIMEOUT] "); Serial.print(coolElapsed/1000);
        Serial.print("s 초과 → IDLE obj1="); Serial.print(objTemp1,1);
        Serial.print(" obj2="); Serial.print(objTemp2,1);
        Serial.print(" amb="); Serial.println(ambTemp,1);
        enterState(IDLE);
        break;
      }

      // 2분(연장 없이) 내 저점 미도달 시: ambTemp-0.5까지 내려가면 재가열
      if (cmd.loopOn && coolAmbMs == 0 && coolElapsed >= 120000UL) {
        float fallback = ambTemp - 0.5f;
        if (objTemp1 <= fallback && objTemp2 <= fallback) {
          coolAmbMs = 0;
          fanOff();
          enterState(HEATING);
          break;
        }
      }

      // 루프 모드: 저점 도달 후 COOL_EXTRA_MS 경과 → HEATING 재진입
      if (cmd.loopOn && coolAmbMs > 0 && millis() - coolAmbMs >= COOL_EXTRA_MS) {
        coolAmbMs = 0;
        fanOff();
        enterState(HEATING);
      }
      // 루프 OFF: 저점 도달 후 COOL_EXTRA_MS 경과 → IDLE
      if (!cmd.loopOn && coolAmbMs > 0 && millis() - coolAmbMs >= COOL_EXTRA_MS) {
        coolAmbMs = 0;
        enterState(IDLE);
      }
      break;
    }

    case SENSOR_ERROR:
      break;  // loop 상단에서 처리됨
  }

  // ── 순차/랜덤 재생 타이머 ────────────────────────────────────────
  if ((presetActive == 6 || presetActive == 7) &&
      now - seqPresetStartMs >= SEQ_PRESET_DURATION_MS) {
    uint8_t savedActive = presetActive;
    if (presetActive == 6) {
      seqIdx = (seqIdx + 1) % SEQ_COUNT;
    } else {
      seqIdx = random(0, SEQ_COUNT);
    }
    seqPresetStartMs = now;
    seqCurrentPreset = SEQ_PRESETS[seqIdx];
    runPreset(seqCurrentPreset);
    presetActive = savedActive;
    Serial.print("[SEQ] → preset "); Serial.println(seqCurrentPreset);
  }

  // ── 스위치 상태 감지 ─────────────────────────────────────────────
  uint8_t swCur = !digitalRead(SW_PIN);  // PULLUP: LOW=눌림=1
  if (swCur != swState) {
    swState = swCur;
    swMs    = now;
    if (swCur) swEverSeen = true;  // ON 전환 시 최초 감지 기록
  }

  // ── XBee 상태 패킷 ~500ms 주기 송신 (랜덤 지터 충돌 분산) ────────────────
  if (now >= nextStatus) {
    nextStatus = now + 450 + random(100); // 450~549ms 후 재전송
    lastStatus = now;
    unsigned long elMs = now - stateMs;
    sendStatus(state, elMs, sensorError, sensorFaultCode,
               objTemp1, objTemp2, swState,
               swEverSeen ? (now - swMs) : 0);
  }
}
