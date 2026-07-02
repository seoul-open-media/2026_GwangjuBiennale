#pragma once
#include <Arduino.h>
#include "config.h"

extern Cmd  cmd;
extern bool newCmd;

void xbeeInit();
void readXBee();
void sendStatus(State currentState, unsigned long elapsedMs,
                bool fault, uint8_t faultCode,
                float temp1, float temp2,
                uint8_t sw, unsigned long swElapsedMs);
