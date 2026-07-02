#pragma once
// ════════════════════════════════════════════════════════════════════
//  output.h  —  SMA / FAN 출력 제어  (Unit1 / Unit2 독립)
//  ⚠ 각 유닛 내에서 SMA와 FAN은 동시 동작 불가
// ════════════════════════════════════════════════════════════════════
#include <Arduino.h>
#include "config.h"

// ── Unit1 상태 플래그 (MLX1 / MOSFET1,2,3)
extern bool sma1Active;
extern bool fan1Active;
// ── Unit2 상태 플래그 (MLX2 / MOSFET4,5,6)
extern bool sma2Active;
extern bool fan2Active;

void outputInit();

// Unit1
void sma1On();
void sma1Off();
void fan1On(uint8_t speed);
void fan1Off();
void all1Off();

// Unit2
void sma2On();
void sma2Off();
void fan2On(uint8_t speed);
void fan2Off();
void all2Off();

void allOff();
