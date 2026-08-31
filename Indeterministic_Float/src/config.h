#pragma once
// ════════════════════════════════════════════════════════════════════
//  config.h  —  핀 / 상수 / 전역 타입 정의
//  Robot: Inditerministic floating
// ════════════════════════════════════════════════════════════════════

// ── 핀 ──────────────────────────────────────────────────────────────
#define SMA_A1_PIN  2   // MOSFET1 ─┐ Group A  (MLX1)
#define SMA_A2_PIN  3   // MOSFET2 ─┘
#define SMA_B1_PIN  4   // MOSFET3 ─┐ Group B  (MLX2)
#define SMA_B2_PIN  5   // MOSFET4 ─┘
#define FAN_PIN     6   // MOSFET5
#define SW_PIN      8   // 리드스위치 (INPUT_PULLUP) — SMA 수축 시 자석 감지

// ── PWM ─────────────────────────────────────────────────────────────
#define SMA_PWM_MAX      70          // (255 기준) — 스태거드 포함 운전 전류 추가 저감
#define SMA_PWM_FREQ    100u         // 100 Hz — 스위칭 EMI 최소화 (초지향성 스피커 노이즈 저감)
#define FAN_PWM_FREQ  15000u         // 15 kHz (팬은 가청 노이즈 방지 위해 고주파 유지)
#define SMA_STAGGER_MS   0u          // 그룹 동시 점화 (0 = 딜레이 없음)
#define SMA_BALANCE_THRESH  2.0f     // 두 센서 허용 온도차 (°C) — 초과 시 앞선 그룹 OFF
#define HEAT_BALANCE_GRACE_MS 3000UL // 가열 시작 후 균형제어 유예 시간 (ms)
#define STAGGER_TARGET_MIN 40u        // 스테거드 목표온도 하한 (°C)
#define STAGGER_TARGET_MAX 50u        // 스테거드 목표온도 상한 (°C)
#define HEAT_MAX_TEMP      50.0f     // 절대 상한 (°C) — 어느 센서든 초과 시 즉시 가열 정지
#define HEAT_MAX_MS        90000UL   // 최대 연속 가열 시간 (ms) — 1.5분 초과 시 강제 냉각
#define COOL_MAX_MS       600000UL   // 최대 냉각 시간 (ms) — 10분 초과 시 강제 IDLE (ambTemp 고착 방어)

// ── XBee ────────────────────────────────────────────────────────────
#define XBEE       Serial1
#define AUDIO_SER  Serial4
#define PKT_LEN         16   // CMD 패킷 (PD→Teensy) 수신 길이
#define STATUS_PKT_LEN  23   // STATUS 패킷 (Teensy→PD) 송신 길이
#define START_B    255
#define END_B      254
#define MY_ROBOT_ID   28 // ★ 유닛마다 1~30 중 해당 번호로 변경하여 업로드

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
#define FAULT_COOL_TIMEOUT  32   // COOLING 최대 시간 초과 (ambTemp 고착 방어)
#define FAULT_WDT_FREEZE    99   // I2C 블로킹 → IntervalTimer가 강제 리셋

// ── 커맨드 구조체 ────────────────────────────────────────────────────
struct Cmd {
  uint8_t mode;           // 1=user_defined  2=play_preset  (b2)
  uint8_t tempTarget;     // 40~50 °C  목표 가열 온도       (b3, user_defined)
  uint8_t sustainSec;     // 0~60 s    유지 시간            (b4)
  uint8_t fanSpeed;       // 0~255     팬 PWM               (b5)
  bool    fanOnly;        // 0/1       팬 전용 모드          (b6)
  uint8_t targetTempMin;  // 10~30 °C  쿨링 저점 온도        (b7, 0=미설정)
  uint8_t heatPwm;        // 0~255     SMA 가열 PWM          (b8, 0=기본값)
  bool    loopOn;         //                                (b12)
  uint8_t presetNum;      //                                (b13)
  uint8_t sound;          // 0~4                           (b14)
};
