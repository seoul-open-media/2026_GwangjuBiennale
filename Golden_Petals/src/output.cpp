#include "output.h"

bool sma1Active = false;
bool fan1Active = false;
bool sma2Active = false;
bool fan2Active = false;

void outputInit() {
  pinMode(SMA_A1_PIN, OUTPUT); pinMode(SMA_A2_PIN, OUTPUT);
  pinMode(FAN1_PIN,   OUTPUT);
  pinMode(SMA_B1_PIN, OUTPUT); pinMode(SMA_B2_PIN, OUTPUT);
  pinMode(FAN2_PIN,   OUTPUT);
  analogWriteFrequency(SMA_A1_PIN, SMA_PWM_FREQ);
  analogWriteFrequency(SMA_A2_PIN, SMA_PWM_FREQ);
  analogWriteFrequency(FAN1_PIN,   FAN_PWM_FREQ);
  analogWriteFrequency(SMA_B1_PIN, SMA_PWM_FREQ);
  analogWriteFrequency(SMA_B2_PIN, SMA_PWM_FREQ);
  analogWriteFrequency(FAN2_PIN,   FAN_PWM_FREQ);
  allOff();
}

// ── Unit1 ────────────────────────────────────────────────────────────
void sma1On() {
  if (fan1Active) { fan1Off(); delay(5); }
  analogWrite(SMA_A1_PIN, SMA_PWM_MAX);
  analogWrite(SMA_A2_PIN, SMA_PWM_MAX);
  sma1Active = true;
}

void sma1Off() {
  analogWrite(SMA_A1_PIN, 0);
  analogWrite(SMA_A2_PIN, 0);
  sma1Active = false;
}

void fan1On(uint8_t speed) {
  if (sma1Active) { sma1Off(); delay(5); }
  analogWrite(FAN1_PIN, speed);
  fan1Active = true;
}

void fan1Off() {
  analogWrite(FAN1_PIN, 0);
  fan1Active = false;
}

void all1Off() { sma1Off(); fan1Off(); }

// ── Unit2 ────────────────────────────────────────────────────────────
void sma2On() {
  if (fan2Active) { fan2Off(); delay(5); }
  // Unit1이 이미 가열 중이면 스태거 딜레이 — 동시 돌입전류로 인한 발열 방지
  if (sma1Active) delay(SMA_STAGGER_MS);
  analogWrite(SMA_B1_PIN, SMA_PWM_MAX);
  analogWrite(SMA_B2_PIN, SMA_PWM_MAX);
  sma2Active = true;
}

void sma2Off() {
  analogWrite(SMA_B1_PIN, 0);
  analogWrite(SMA_B2_PIN, 0);
  sma2Active = false;
}

void fan2On(uint8_t speed) {
  if (sma2Active) { sma2Off(); delay(5); }
  analogWrite(FAN2_PIN, speed);
  fan2Active = true;
}

void fan2Off() {
  analogWrite(FAN2_PIN, 0);
  fan2Active = false;
}

void all2Off() { sma2Off(); fan2Off(); }

void allOff() { all1Off(); all2Off(); }

