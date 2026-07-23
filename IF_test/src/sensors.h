#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MLX90614.h>
#include "config.h"

extern bool    mlx1Ok, mlx2Ok;
extern float   objTemp1, objTemp2, ambTemp, ambTemp2;
extern bool    sensorError;    // true = 센서 오류 발생
extern uint8_t sensorErrorId;  // 1=MLX1, 2=MLX2, 3=둘 다 (레거시)
extern uint8_t sensorFaultCode; // FAULT_* 코드 (config.h 참고)

void sensorsInit();
void readTemps(State currentState);
void sensorsResetEma2();  // HEATING 진입 시 호출 — 이전 사이클 EMA 잔류 방지
bool sensorsAutoRecover(); // SENSOR_ERROR 자동 복구 시도, true=성공 (INIT 계열 제외)
void wire2FreeBus();       // Wire2 버스 강제 해제 (MLX2 에러 복구용)
