#pragma once
// ════════════════════════════════════════════════════════════════════
//  output.h  —  SMA / FAN 출력 제어
//  ⚠ SMA와 FAN은 절대 동시 동작 불가 (하드 뮤텍스)
// ════════════════════════════════════════════════════════════════════
#include <Arduino.h>
#include "config.h"

extern bool smaActive;
extern bool smaActiveA;  // Group A (MLX1)
extern bool smaActiveB;  // Group B (MLX2)
extern bool fanActive;

void outputInit();
void smaOff();
void smaOffA();
void smaOffB();
void smaOnA();
void smaOnB();
void fanOff();
void allOff();
void smaOn();
void fanOn(uint8_t speed);
