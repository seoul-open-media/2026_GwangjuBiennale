#pragma once
// ════════════════════════════════════════════════════════════════════
//  config.h  —  핀 / 상수 / 전역 타입 정의
//  Robot: Golden Petal
// ════════════════════════════════════════════════════════════════════

// ── 핀 ──────────────────────────────────────────────────────────────
// Unit 1: MOSFET1,2=SMA  MOSFET3=FAN  (MLX1 = Wire)
#define SMA_A1_PIN  2   // MOSFET1 ─┐ Unit1 SMA
#define SMA_A2_PIN  3   // MOSFET2 ─┘
#define FAN1_PIN    4   // MOSFET3    Unit1 FAN
// Unit 2: MOSFET4,5=SMA  MOSFET6=FAN  (MLX2 = Wire2)
#define SMA_B1_PIN  5   // MOSFET4 ─┐ Unit2 SMA
#define SMA_B2_PIN  6   // MOSFET5 ─┘
#define FAN2_PIN    7   // MOSFET6    Unit2 FAN
#define SW_PIN      8   // 리드스위치 (INPUT_PULLUP)

// ── PWM ─────────────────────────────────────────────────────────────
#define SMA_PWM_MAX      70          // (255 기준) — 전원부 부담 경감
#define SMA_PWM_FREQ    100u         // 100 Hz — 스위칭 EMI 최소화 (초지향성 스피커 노이즈 저감)
#define FAN_PWM_FREQ  15000u         // 15 kHz (팬은 가청 노이즈 방지 위해 고주파 유지)
#define SMA_STAGGER_MS  20u          // 그룹 A→B 순차 점화 딜레이 (ms)
#define SMA_BALANCE_THRESH  5.0f     // S2가 S1보다 이 온도(°C) 이상 앞서면 B 그룹 OFF
#define HEAT_MAX_TEMP  55.0f         // 절대 온도 상한 (°C) — 초과 시 즉시 가열 차단
#define HEAT_MAX_MS    60000UL       // 최대 연속 가열 시간 (ms) — 1분 초과 시 강제 냉각

// ── XBee ────────────────────────────────────────────────────────────
#define XBEE       Serial1
#define AUDIO_SER  Serial4
#define PKT_LEN         16   // CMD 패킷 (PD→Teensy) 수신 길이
#define STATUS_PKT_LEN  23   // STATUS 패킷 (Teensy→PD) 송신 길이
#define START_B    255
#define END_B      254
#define MY_ROBOT_ID 11  // ★ 유닛마다 1 ~ 12 로 변경하여 업로드

// ── 상태 열거형 ──────────────────────────────────────────────────────
enum State { IDLE, HEATING, SUSTAINING, COOLING, SENSOR_ERROR };

// ── faultCode decimal table (sendStatus b3) ────────────────────────────
//   0 : OK — no fault
//  11 : MLX1 consecutive read failure (FAIL)
//  12 : MLX1 value frozen — no change detected (FROZEN)
//  13 : MLX1 not found at init (INIT)
//  21 : MLX2 consecutive read failure (FAIL)
//  22 : MLX2 value frozen (FROZEN)
//  23 : MLX2 not found at init (INIT)
//  24 : MLX2 I2C bus recovery failed
//  31 : both MLX1 & MLX2 not found at init
#define FAULT_NONE           0
#define FAULT_MLX1_FAIL     11
#define FAULT_MLX1_FROZEN   12
#define FAULT_MLX1_INIT     13
#define FAULT_MLX2_FAIL     21
#define FAULT_MLX2_FROZEN   22
#define FAULT_MLX2_INIT     23
#define FAULT_MLX2_I2C      24
#define FAULT_BOTH_INIT     31
#define FAULT_MLX1_SPIKE    14   // MLX1 온도 5°C 초과 스파이크 감지
#define FAULT_MLX2_SPIKE    25   // MLX2 온도 5°C 초과 스파이크 감지
#define FAULT_WDT_FREEZE    99   // 워치독 타임아웃 리셋 감지

// ── 안전 상수 ───────────────────────────────────────────────────────
#define COOL_MAX_MS    600000UL  // 최대 냉각 시간 10분 — ambTemp 고착 시 무한 냉각 방지

// ── 커맨드 구조체 ────────────────────────────────────────────────────
struct Cmd {
  uint8_t mode;        // 1=user_defined  2=play_preset
  uint8_t tempTarget;  // 10~60 °C  (Unit1)  b3
  uint8_t sustainSec;  // 0~60 s    (Unit1)  b4
  uint8_t fanSpeed;    // 0~255     (Unit1)  b5
  bool    fanOnly1;    //           (Unit1)  b6
  uint8_t tempTarget2; // 10~60 °C  (Unit2)  b7
  uint8_t sustainSec2; // 0~60 s    (Unit2)  b8
  uint8_t fanSpeed2;   // 0~255     (Unit2)  b9
  bool    fanOnly2;    //           (Unit2)  b10
  bool    loopOn;      //                    b12
  uint8_t presetNum;   //                    b13
  uint8_t sound;       //                    b14
};
