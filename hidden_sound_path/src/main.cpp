// ════════════════════════════════════════════════════════════════════
//  main.cpp  —  Hidden Sound Path
//  Teensy 4.0 + Teensy Audio Shield (SGTL5000)
//
//  전원 인가 즉시 SOUND1.WAV 무한 루프 재생
//  외부 명령 없음 — 독립 동작
// ════════════════════════════════════════════════════════════════════
#include <Arduino.h>
#include <Audio.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <SerialFlash.h>
#include "config.h"

// ── Audio 오브젝트 ─────────────────────────────────────────────────────
AudioPlaySdWav        wav;
AudioMixer4           mixL;
AudioMixer4           mixR;
AudioOutputI2S        i2sOut;
AudioControlSGTL5000  sgtl5000;

AudioConnection p1(wav,  0, mixL, 0);   // wav L → mixL ch0
AudioConnection p2(wav,  1, mixR, 0);   // wav R → mixR ch0
AudioConnection p3(mixL, 0, i2sOut, 0); // mixL  → i2sOut L
AudioConnection p4(mixR, 0, i2sOut, 1); // mixR  → i2sOut R

static bool sdOk = false;

// ════════════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 2000) {}

  AudioMemory(AUDIO_MEM);

  sgtl5000.enable();
  sgtl5000.volume(VOLUME);
  sgtl5000.dacVolume(DAC_VOLUME);

  // 미서 모든 채널 게인 1.0
  for (uint8_t i = 0; i < 4; i++) {
    mixL.gain(i, 1.0f);
    mixR.gain(i, 1.0f);
  }

  Serial.print("[BOOT] sgtl5000 vol="); Serial.print(VOLUME);
  Serial.print(" dac="); Serial.println(DAC_VOLUME);

  if (!SD.begin(SDCS_PIN)) {
    Serial.println("[ERR] SD init failed — 재시도: loop에서 5초마다");
    return;  // sdOk = false 유지, loop에서 재시도
  }
  sdOk = true;

  Serial.print("[SD]  "); Serial.print(SOUND_FILE);
  Serial.println(SD.exists(SOUND_FILE) ? " : OK" : " : MISSING");

  if (!SD.exists(SOUND_FILE)) return;

  Serial.print("[BOOT] ready — looping "); Serial.println(SOUND_FILE);
  wav.play(SOUND_FILE);
}

// ════════════════════════════════════════════════════════════════════
void loop() {
  // SD 초기화 실패 시 5초마다 재시도
  if (!sdOk) {
    delay(5000);
    if (SD.begin(SDCS_PIN) && SD.exists(SOUND_FILE)) {
      sdOk = true;
      Serial.println("[SD] 재시도 성공");
      wav.play(SOUND_FILE);
    } else {
      Serial.println("[SD] 재시도 실패...");
    }
    return;
  }
  // 재생 중 상태 디버그 (5초마다)
  static unsigned long lastDbg = 0;
  if (millis() - lastDbg > 5000) {
    lastDbg = millis();
    Serial.print("[DBG] isPlaying="); Serial.println(wav.isPlaying());
  }
  // 재생이 끝나면 즉시 다시 재생
  if (!wav.isPlaying()) {
    wav.play(SOUND_FILE);
    Serial.println("[LOOP] restart");
  }
}
