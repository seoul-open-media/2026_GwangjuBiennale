// ════════════════════════════════════════════════════════════════════
//  main.cpp  —  setup / loop
//  Golden Petal
//  Teensy 4.x  |  robot_id: 1~12
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
    cfg.timeout = 4.0;  // 4초 이내 kick 없으면 하드웨어 리셋
    cfg.trigger = 0.0;  // 인터럽트 없이 바로 리셋
    wdt.begin(cfg);
}
static void wdtKick() { wdt.feed(); }

// ── Unit1 상태 ───────────────────────────────────────────────────────
State         state1   = IDLE;
unsigned long stateMs1 = 0;

// ── Unit2 상태 ───────────────────────────────────────────────────────
State         state2   = IDLE;
unsigned long stateMs2 = 0;

unsigned long lastStatus = 0;
unsigned long nextStatus = 0;  // 랜덤 지터 적용 다음 전송 시각
unsigned long lastHB     = 0;   // heartbeat / 센서 디버그
uint8_t       presetActive = 0;  // 현재 실행 중인 프리셋 (0=없음)
uint8_t       swState    = 0;
unsigned long swMs       = 0;
bool          swEverSeen = false;
unsigned long unit2PendingAt = 0;   // Unit2 지연 시작 타이머 (0=없음)
#define U2_PHASE_DELAY_MS 10000UL   // Unit2 시작 오프셋 (ms, 조정 가능)

// ── 순차/랜덤 재생 (프리셋 6/7) ─────────────────────────────────────
static const uint8_t SEQ_PRESETS[] = {2, 3, 5};  // 순환할 프리셋 목록
static const uint8_t SEQ_COUNT     = 3;
uint8_t       seqIdx           = 0;
uint8_t       seqCurrentPreset = 2;
unsigned long seqPresetStartMs = 0;
#define SEQ_PRESET_DURATION_MS 120000UL  // 프리셋당 재생 시간 (ms, 기본 2분)

// ── 상태 전환 ────────────────────────────────────────────────────────
void enterState1(State s) {
  state1   = s;
  stateMs1 = millis();
  switch (s) {
    case IDLE:         all1Off();             break;
    case HEATING:      sma1On();              break;
    case SUSTAINING:   sma1Off();              break;  // fan keeps running
    case COOLING:      fan1On(cmd.fanSpeed);  break;
    case SENSOR_ERROR: all1Off();             break;
  }
  const char* nm = s==IDLE?"IDLE":s==HEATING?"HEAT":s==SUSTAINING?"SUST":s==COOLING?"COOL":"ERR!";
  Serial.print("[U1 STATE] -> "); Serial.println(nm);
}

void enterState2(State s) {
  state2   = s;
  stateMs2 = millis();
  switch (s) {
    case IDLE:         all2Off();              break;
    case HEATING:      sma2On();               break;
    case SUSTAINING:   sma2Off();               break;  // fan keeps running
    case COOLING:      fan2On(cmd.fanSpeed2);  break;
    case SENSOR_ERROR: all2Off();              break;
  }
  const char* nm = s==IDLE?"IDLE":s==HEATING?"HEAT":s==SUSTAINING?"SUST":s==COOLING?"COOL":"ERR!";
  Serial.print("[U2 STATE] -> "); Serial.println(nm);
}

void sendSound(uint8_t s) {
  AUDIO_SER.write(s);
  Serial.print("[SOUND] "); Serial.println(s);
}

// ── 프리셋 실행 (6/7번 순차·랜덤에서도 내부 호출) ──────────────────────
void runPreset(uint8_t n) {
  unsigned long t = millis();
  switch (n) {
    case 1:
      Serial.println("[PRESET 1] 정지");
      presetActive = 0; unit2PendingAt = 0;
      all1Off(); state1 = IDLE; stateMs1 = t;
      all2Off(); state2 = IDLE; stateMs2 = t;
      cmd.loopOn = false;
      break;
    case 2:
      Serial.println("[PRESET 2] 유영");
      presetActive = 0; unit2PendingAt = 0;
      cmd.tempTarget=38; cmd.sustainSec=20; cmd.fanSpeed=130; cmd.fanOnly1=false;
      cmd.tempTarget2=40; cmd.sustainSec2=25; cmd.fanSpeed2=155; cmd.fanOnly2=false;
      cmd.loopOn = true;
      enterState1(HEATING);
      unit2PendingAt = t + U2_PHASE_DELAY_MS;
      all2Off(); state2=IDLE; stateMs2=t;
      break;
    case 3:
      Serial.println("[PRESET 3] 느린 유영");
      presetActive = 0; unit2PendingAt = 0;
      cmd.tempTarget=36; cmd.sustainSec=45; cmd.fanSpeed=100; cmd.fanOnly1=false;
      cmd.tempTarget2=36; cmd.sustainSec2=45; cmd.fanSpeed2=110; cmd.fanOnly2=false;
      cmd.loopOn = true;
      enterState1(HEATING);
      unit2PendingAt = t + U2_PHASE_DELAY_MS;
      all2Off(); state2=IDLE; stateMs2=t;
      break;
    case 4:
      Serial.println("[PRESET 4] 교번 유영");
      presetActive = 2; unit2PendingAt = 0;
      cmd.tempTarget=40; cmd.sustainSec=0; cmd.fanSpeed=150; cmd.fanOnly1=false;
      cmd.tempTarget2=40; cmd.sustainSec2=0; cmd.fanSpeed2=150; cmd.fanOnly2=false;
      cmd.loopOn = false;
      all2Off(); state2=IDLE; stateMs2=t;
      enterState1(HEATING);
      break;
    case 5:
      Serial.println("[PRESET 5] 활발한 유영");
      presetActive = 0; unit2PendingAt = 0;
      cmd.tempTarget=42; cmd.sustainSec=8; cmd.fanSpeed=200; cmd.fanOnly1=false;
      cmd.tempTarget2=42; cmd.sustainSec2=12; cmd.fanSpeed2=220; cmd.fanOnly2=false;
      cmd.loopOn = true;
      enterState1(HEATING);
      unit2PendingAt = t + U2_PHASE_DELAY_MS;
      all2Off(); state2=IDLE; stateMs2=t;
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

  Serial.println("[BOOT] Golden Petal");
  Serial.println("[BOOT] robot_id 1~12 | XBee Serial1 115200bps");

  // ── 워치독 타임아웃 리셋 감지 (SRC_SRSR)
  // sensorsInit() 이후에 덮어써야 센서 정상이어도 WDT 리셋을 보고할 수 있음
  if (SRC_SRSR & SRC_SRSR_WDOG_RST_B) {
    SRC_SRSR = SRC_SRSR_WDOG_RST_B;  // W1C: 클리어 (다음 리셋과 혼동 방지)
    sensorFaultCode = FAULT_WDT_FREEZE;
    sensorError     = true;
    Serial.println("[WDT] 워치독 타임아웃 리셋 감지");
  }
  wdtInit();

  enterState1(IDLE);
  enterState2(IDLE);
}

// ════════════════════════════════════════════════════════════════════
void loop() {
  wdtKick();  // 워치독 타임아웃 방지 — 여기를 지나지 못하면 4초 후 리셋
  unsigned long now = millis();

  readXBee();
  readTemps(state1, state2);  // 각 유닛 상태 독립 전달 (EMA downLimit 분리)

  // ── 센서 오류 감지 ───────────────────────────────────────────────
  if (sensorError) {
    if (state1 != SENSOR_ERROR) { all1Off(); state1 = SENSOR_ERROR; stateMs1 = now; }
    if (state2 != SENSOR_ERROR) { all2Off(); state2 = SENSOR_ERROR; stateMs2 = now; }
    // 오류 상태에서도 XBee 상태 패킷 송신 (PD/대시보드 에러 감지용)
    if (now >= nextStatus) {
      nextStatus = now + 450 + random(100);
      sendStatus(state1, now - stateMs1,
                 state2, now - stateMs2,
                 sensorError, sensorFaultCode,
                 objTemp1, objTemp2,
                 swState, swEverSeen ? (now - swMs) : 0);
    }
    // ── 자동 복구: 10초 후 센서 재초기화 시도 (최대 5회, INIT 계열 제외)
    static uint8_t autoRecoverCnt = 0;
    if (autoRecoverCnt < 5 && now - stateMs1 >= 10000UL) {
      ++autoRecoverCnt;
      Serial.print("[AUTO-RECOVER] 시도 #"); Serial.println(autoRecoverCnt);
      if (sensorsAutoRecover()) {
        Serial.println("[AUTO-RECOVER] 성공 → IDLE 복귀");
        // autoRecoverCnt 리셋 안 함 — 5회 소진 후 에러 영구 유지
        all1Off(); state1 = IDLE; stateMs1 = now;
        all2Off(); state2 = IDLE; stateMs2 = now;
        return;
      }
      stateMs1 = now; stateMs2 = now;  // 실패 시 10초 타이머 재시작
    }
    // RESET 커맨드는 SENSOR_ERROR에서도 처리
    // 조건: mode=2 preset=1 (정지) 또는 전체 제어 파라미터 0
    if (newCmd) {
      bool isReset = (cmd.mode == 2 && cmd.presetNum == 1) ||
                     (cmd.fanSpeed == 0 && cmd.tempTarget == 0 &&
                      cmd.fanSpeed2 == 0 && cmd.tempTarget2 == 0);
      if (isReset) {
        newCmd          = false;
        sensorError     = false;
        sensorFaultCode = FAULT_NONE;
        autoRecoverCnt  = 0;
        all1Off(); state1 = IDLE; stateMs1 = now;
        all2Off(); state2 = IDLE; stateMs2 = now;
        return;
      }
    }
    return;
  }

  // ── 새 커맨드 처리 ───────────────────────────────────────────────
  if (newCmd) {
    newCmd = false;
    if (cmd.sound > 0) sendSound(cmd.sound);

    // ── 프리셋 모드 (mode=2) ─────────────────────────────────────
    if (cmd.mode == 2) {
      uint8_t pn = cmd.presetNum;
      if (pn >= 1 && pn <= 5) {
        runPreset(pn);
      } else if (pn == 6) {
        // 순차 재생: SEQ_PRESETS[] 순서대로 각 SEQ_PRESET_DURATION_MS마다 전환
        Serial.println("[PRESET 6] 순차 재생 (2→3→5→...)");
        seqIdx = 0; seqCurrentPreset = SEQ_PRESETS[0];
        seqPresetStartMs = millis();
        runPreset(seqCurrentPreset);
        presetActive = 6;  // runPreset이 덮어쓴 presetActive 복원
      } else if (pn == 7) {
        // 랜덤 재생: SEQ_PRESETS[] 중 매번 다른 프리셋 선택
        Serial.println("[PRESET 7] 랜덤 재생");
        seqIdx = random(0, SEQ_COUNT);
        seqCurrentPreset = SEQ_PRESETS[seqIdx];
        seqPresetStartMs = millis();
        runPreset(seqCurrentPreset);
        presetActive = 7;  // runPreset이 덮어쓴 presetActive 복원
      } else {
        Serial.print("[PRESET] unknown: "); Serial.println(pn);
      }
      return;  // 프리셋 처리 후 아래 user_defined 로직 스킵
    }

    // ── Unit1 커맨드 처리
    if (cmd.fanOnly1) {
      Serial.println("[U1] fanOnly → COOL");
      enterState1(COOLING);
    } else if (cmd.fanSpeed == 0 && cmd.tempTarget == 0) {
      Serial.println("[U1] allOff → IDLE");
      all1Off(); state1 = IDLE; stateMs1 = millis();
    } else if (cmd.fanSpeed == 0 && state1 == COOLING) {
      Serial.println("[U1] fan stop → IDLE");
      all1Off(); state1 = IDLE; stateMs1 = millis();
    } else if ((state1 == HEATING || state1 == SUSTAINING) &&
               cmd.tempTarget < (uint8_t)objTemp1) {
      Serial.print("[U1] lower target → COOL ("); Serial.print(objTemp1,1); Serial.println("C)");
      enterState1(COOLING);
    } else {
      enterState1(HEATING);
    }

    // ── Unit2 커맨드 처리
    if (cmd.fanOnly2) {
      unit2PendingAt = 0;
      Serial.println("[U2] fanOnly → COOL");
      enterState2(COOLING);
    } else if (cmd.fanSpeed2 == 0 && cmd.tempTarget2 == 0) {
      unit2PendingAt = 0;
      Serial.println("[U2] allOff → IDLE");
      all2Off(); state2 = IDLE; stateMs2 = millis();
    } else if (cmd.fanSpeed2 == 0 && state2 == COOLING) {
      unit2PendingAt = 0;
      Serial.println("[U2] fan stop → IDLE");
      all2Off(); state2 = IDLE; stateMs2 = millis();
    } else if ((state2 == HEATING || state2 == SUSTAINING) &&
               cmd.tempTarget2 < (uint8_t)objTemp2) {
      unit2PendingAt = 0;
      Serial.print("[U2] lower target → COOL ("); Serial.print(objTemp2,1); Serial.println("C)");
      enterState2(COOLING);
    } else {
      // Unit2를 U2_PHASE_DELAY_MS 후에 시작 (Unit1과 위상 차이)
      unit2PendingAt = now + U2_PHASE_DELAY_MS;
      all2Off(); state2 = IDLE; stateMs2 = now;
      Serial.print("[U2] delayed start in "); Serial.print(U2_PHASE_DELAY_MS / 1000); Serial.println("s");
    }
  }

  // ── Unit2 지연 시작 체크 ─────────────────────────────────────────
  if (unit2PendingAt && now >= unit2PendingAt && state2 == IDLE) {
    unit2PendingAt = 0;
    enterState2(HEATING);
  }

  // ── Unit1 상태 머신 ──────────────────────────────────────────────
  switch (state1) {
    case IDLE: break;

    case HEATING: {
      bool t1ok = objTemp1 >= (float)cmd.tempTarget;
      bool t1over = objTemp1 > HEAT_MAX_TEMP;
      bool t1timeout = (now - stateMs1) > HEAT_MAX_MS;
      if (t1over) {
        Serial.print("[U1 HEAT] OVER-TEMP "); Serial.print(objTemp1, 1); Serial.println("C → COOL");
        enterState1(COOLING);
      } else if (t1timeout) {
        Serial.println("[U1 HEAT] TIMEOUT → COOL");
        enterState1(COOLING);
      } else if (t1ok) {
        sma1Off();
        Serial.print("[U1 HEAT] target reached "); Serial.print(objTemp1, 1);
        Serial.print("C tgt="); Serial.println(cmd.tempTarget);
        enterState1(COOLING);
      } else {
        if (!sma1Active) {
          Serial.print("[U1 HEAT] sma1On s1="); Serial.print(objTemp1,1);
          Serial.print("C tgt="); Serial.println(cmd.tempTarget);
          sma1On();
        }
      }
      break;
    }

    case SUSTAINING: {
      unsigned long elapsed = (now - stateMs1) / 1000UL;
      if (elapsed >= (unsigned long)cmd.sustainSec) {
        Serial.println("[U1 SUST] done");
        if (cmd.loopOn)  enterState1(HEATING);
        else             enterState1(IDLE);
      }
      break;
    }

    case COOLING: {
      // COOLING 타임아웃 — ambTemp 고착 시 무한 냉각 방지
      if (now - stateMs1 >= COOL_MAX_MS) {
        Serial.println("[U1 COOL] TIMEOUT → IDLE");
        enterState1(IDLE);
        break;
      }
      // fanOnly1 모드: 저점 체크 없이 FAN_OFF/RESET 올 때까지 유지
      if (!cmd.fanOnly1) {
        float ambAvg = (ambTemp + ambTemp2) / 2.0f;
        if (objTemp1 <= ambAvg + 2.0f) {
          Serial.print("[U1 COOL] low point reached "); Serial.println(objTemp1, 1);
          if (cmd.sustainSec > 0) enterState1(SUSTAINING);
          else if (cmd.loopOn)    enterState1(HEATING);
          else                    enterState1(IDLE);
        }
      }
      break;
    }

    case SENSOR_ERROR: break;
  }

  // ── Unit2 상태 머신 ──────────────────────────────────────────────
  switch (state2) {
    case IDLE: break;

    case HEATING: {
      bool t2ok = objTemp2 >= (float)cmd.tempTarget2;
      bool t2over = objTemp2 > HEAT_MAX_TEMP;
      bool t2timeout = (now - stateMs2) > HEAT_MAX_MS;
      if (t2over) {
        Serial.print("[U2 HEAT] OVER-TEMP "); Serial.print(objTemp2, 1); Serial.println("C → COOL");
        enterState2(COOLING);
      } else if (t2timeout) {
        Serial.println("[U2 HEAT] TIMEOUT → COOL");
        enterState2(COOLING);
      } else if (t2ok) {
        sma2Off();
        Serial.print("[U2 HEAT] target reached "); Serial.println(objTemp2, 1);
        enterState2(COOLING);
      } else {
        if (!sma2Active) sma2On();
      }
      break;
    }

    case SUSTAINING: {
      unsigned long elapsed = (now - stateMs2) / 1000UL;
      if (elapsed >= (unsigned long)cmd.sustainSec2) {
        Serial.println("[U2 SUST] done");
        if (cmd.loopOn)  enterState2(HEATING);
        else             enterState2(IDLE);
      }
      break;
    }

    case COOLING: {
      // COOLING 타임아웃 — ambTemp 고착 시 무한 냉각 방지
      if (now - stateMs2 >= COOL_MAX_MS) {
        Serial.println("[U2 COOL] TIMEOUT → IDLE");
        enterState2(IDLE);
        break;
      }
      // fanOnly2 모드: 저점 체크 없이 FAN_OFF/RESET 올 때까지 유지
      if (!cmd.fanOnly2) {
        float ambAvg = (ambTemp + ambTemp2) / 2.0f;
        if (objTemp2 <= ambAvg + 2.0f) {
          Serial.print("[U2 COOL] low point reached "); Serial.println(objTemp2, 1);
          if (cmd.sustainSec2 > 0) enterState2(SUSTAINING);
          else if (cmd.loopOn)     enterState2(HEATING);
          else                     enterState2(IDLE);
        }
      }
      break;
    }

    case SENSOR_ERROR: break;
  }

  // ── 프리셋 교번 로직 (2: 교번 호흡, 5: 파도) ─────────────────────
  if (presetActive == 2) {
    float ambAvg = (ambTemp + ambTemp2) / 2.0f;
    // 냉각 완료 판정: 주변온도 + 2°C 이하면 팬 끄고 IDLE
    if (state1 == COOLING && objTemp1 <= ambAvg + 2.0f) {
      fan1Off(); state1 = IDLE; stateMs1 = millis();
      Serial.println("[PRESET ALT] U1 cooled → IDLE");
    }
    if (state2 == COOLING && objTemp2 <= ambAvg + 2.0f) {
      fan2Off(); state2 = IDLE; stateMs2 = millis();
      Serial.println("[PRESET ALT] U2 cooled → IDLE");
    }
    // 교번: 한쪽이 IDLE이 되면 반대쪽 HEATING 시작
    if (state1 == IDLE && state2 == COOLING) {
      enterState1(HEATING);
      Serial.println("[PRESET ALT] U2 cooling → U1 HEAT start");
    }
    if (state2 == IDLE && state1 == COOLING) {
      enterState2(HEATING);
      Serial.println("[PRESET ALT] U1 cooling → U2 HEAT start");
    }
  }

  // 루프 모드 COOLING→HEATING 전환은 각 유닛의 COOLING 케이스에서 처리

  // ── 순차/랜덤 재생 타이머 (6: 순차, 7: 랜덤) ────────────────────────
  if ((presetActive == 6 || presetActive == 7) &&
      now - seqPresetStartMs >= SEQ_PRESET_DURATION_MS) {
    uint8_t savedActive = presetActive;
    if (savedActive == 6) {
      seqIdx = (seqIdx + 1) % SEQ_COUNT;
    } else {
      uint8_t nextIdx = seqIdx;
      while (nextIdx == seqIdx) nextIdx = (uint8_t)random(0, SEQ_COUNT);
      seqIdx = nextIdx;
    }
    seqCurrentPreset = SEQ_PRESETS[seqIdx];
    seqPresetStartMs = now;
    Serial.print("[SEQ] → preset "); Serial.println(seqCurrentPreset);
    runPreset(seqCurrentPreset);
    presetActive = savedActive;  // runPreset 후 6/7 복원
  }

  // ── 스위치 감지 ──────────────────────────────────────────────────
  uint8_t swCur = !digitalRead(SW_PIN);
  if (swCur != swState) {
    swState = swCur;
    swMs    = now;
    if (swCur) swEverSeen = true;
  }

  // ── 센서 노이즈 분석 로그 300ms ────────────────────────────
  if (now - lastHB >= 300) {
    lastHB = now;
    float diff = rawTemp2 - objTemp2;          // 원시 - 필터링 차이
    Serial.print(now);      Serial.print(" ");
    Serial.print(objTemp1, 2); Serial.print(" ");
    Serial.print(rawTemp2, 2); Serial.print(" ");
    Serial.print(objTemp2, 2); Serial.print(" ");
    Serial.println(diff, 2);
    // 형식: millis S1_filtered S2_raw S2_filtered S2_diff
  }

  // ── XBee 상태 패킷 ~500ms 주기 송신 (랜덤 지터 충돌 분산) ──────────────
  if (now >= nextStatus) {
    nextStatus = now + 450 + random(100); // 450~549ms 후 재전송
    lastStatus = now;
    sendStatus(state1, now - stateMs1,
               state2, now - stateMs2,
               sensorError, sensorFaultCode,
               objTemp1, objTemp2,
               swState, swEverSeen ? (now - swMs) : 0);
  }
}
