#pragma once
#include <Arduino.h>

void oledInit();
void oledUpdate(unsigned long stateMs);
void oledError(uint8_t sensorId, uint8_t faultCode);  // 센서 오류 전용 화면
