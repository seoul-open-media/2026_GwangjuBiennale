#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MLX90614.h>
#include "config.h"

extern bool    mlx1Ok, mlx2Ok;
extern float   objTemp1, objTemp2, ambTemp, ambTemp2;
extern float   rawTemp2;   // EMA 필터 전 원시값 — 노이즈 분석용
extern bool    sensorError;    // true = 센서 오류 발생
extern uint8_t sensorErrorId;  // 1=MLX1, 2=MLX2, 3=둘 다 (레거시)
extern uint8_t sensorFaultCode; // FAULT_* 코드 (config.h 참고)

void sensorsInit();
void readTemps(State s1, State s2);  // s1=Unit1 상태, s2=Unit2 상태 (EMA 방향별 독립 적용)
void wire2FreeBus();       // Wire2 버스 강제 해제 (bit-bang SCL 9펄스)
bool sensorsAutoRecover(); // SENSOR_ERROR 자동 복구 시도, 성공 시 true
