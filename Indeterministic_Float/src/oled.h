#pragma once
#include <Arduino.h>

void oledInit();
void oledUpdate(unsigned long stateMs);
void oledError(uint8_t sensorId);   // 센서 오류 전용 화면
