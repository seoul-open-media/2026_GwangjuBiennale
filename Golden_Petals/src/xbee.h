#pragma once
#include <Arduino.h>
#include "config.h"

extern Cmd  cmd;
extern bool newCmd;

void xbeeInit();
void readXBee();
void sendStatus(State state1, unsigned long elapsedMs1,
                State state2, unsigned long elapsedMs2,
                bool fault, uint8_t faultCode,
                float temp1, float temp2,
                uint8_t sw, unsigned long swElapsedMs);
