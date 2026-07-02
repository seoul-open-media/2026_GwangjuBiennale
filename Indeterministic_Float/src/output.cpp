#include "output.h"

bool smaActive  = false;
bool smaActiveA = false;
bool smaActiveB = false;
bool fanActive  = false;

void outputInit() {
  pinMode(SMA_A1_PIN, OUTPUT); pinMode(SMA_A2_PIN, OUTPUT);
  pinMode(SMA_B1_PIN, OUTPUT); pinMode(SMA_B2_PIN, OUTPUT);
  pinMode(FAN_PIN,    OUTPUT);
  analogWriteFrequency(SMA_A1_PIN, SMA_PWM_FREQ);
  analogWriteFrequency(SMA_A2_PIN, SMA_PWM_FREQ);
  analogWriteFrequency(SMA_B1_PIN, SMA_PWM_FREQ);
  analogWriteFrequency(SMA_B2_PIN, SMA_PWM_FREQ);
  analogWriteFrequency(FAN_PIN,    FAN_PWM_FREQ);
  allOff();
}

void smaOffA() {
  analogWrite(SMA_A1_PIN, 0);
  analogWrite(SMA_A2_PIN, 0);
  smaActiveA = false;
  smaActive  = smaActiveA || smaActiveB;
}

void smaOffB() {
  analogWrite(SMA_B1_PIN, 0);
  analogWrite(SMA_B2_PIN, 0);
  smaActiveB = false;
  smaActive  = smaActiveA || smaActiveB;
}

void smaOnA() {
  if (fanActive) { fanOff(); delay(5); }
  analogWrite(SMA_A1_PIN, SMA_PWM_MAX);
  analogWrite(SMA_A2_PIN, SMA_PWM_MAX);
  smaActiveA = true;
  smaActive  = true;
}

void smaOnB() {
  if (fanActive) { fanOff(); delay(5); }
  analogWrite(SMA_B1_PIN, SMA_PWM_MAX);
  analogWrite(SMA_B2_PIN, SMA_PWM_MAX);
  smaActiveB = true;
  smaActive  = true;
}

void smaOff() {
  analogWrite(SMA_A1_PIN, 0);
  analogWrite(SMA_A2_PIN, 0);
  analogWrite(SMA_B1_PIN, 0);
  analogWrite(SMA_B2_PIN, 0);
  smaActive = smaActiveA = smaActiveB = false;
}

void fanOff() {
  analogWrite(FAN_PIN, 0);
  fanActive = false;
}

void allOff() { smaOff(); fanOff(); }

void smaOn() {
  if (fanActive) { fanOff(); delay(5); }  // FAN 먼저 끄기
  // Group A 먼저, 딜레이 후 Group B → 동시 돌입전류 분산
  analogWrite(SMA_A1_PIN, SMA_PWM_MAX);
  analogWrite(SMA_A2_PIN, SMA_PWM_MAX);
  smaActiveA = true;
  delay(SMA_STAGGER_MS);
  analogWrite(SMA_B1_PIN, SMA_PWM_MAX);
  analogWrite(SMA_B2_PIN, SMA_PWM_MAX);
  smaActiveB = true;
  smaActive  = true;
}

void fanOn(uint8_t speed) {
  if (smaActive) { smaOff(); delay(5); }  // SMA 먼저 끄기
  analogWrite(FAN_PIN, speed);
  fanActive = true;
}
