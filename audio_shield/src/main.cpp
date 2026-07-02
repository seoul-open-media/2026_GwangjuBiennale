// ════════════════════════════════════════════════════════════════════
//  main.cpp  —  Audio Shield Controller
//  Teensy 4.0 + Teensy Audio Shield (SGTL5000)
//
//  배선:
//    로봇 Teensy  Serial2 TX (pin 17) →  Audio Teensy  Serial1 RX (pin 0)
//    GND ────────────────────────────────────────────── GND
// ════════════════════════════════════════════════════════════════════
#include <Arduino.h>
#include <Audio.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <SerialFlash.h>
#include "config.h"

// ── SD 파일 목록 ──────────────────────────────────────────────────────
static const char* SOUND_FILES[SOUND_COUNT] = {
  "SOUND1.WAV", "SOUND2.WAV", "SOUND3.WAV", "SOUND4.WAV"
};

// ── Audio 오브젝트 ─────────────────────────────────────────────────────
//  wav1~4 → mixL/mixR (각 ch 0~3) → i2sOut (L/R)
AudioPlaySdWav        wav1;
AudioPlaySdWav        wav2;
AudioPlaySdWav        wav3;
AudioPlaySdWav        wav4;
AudioMixer4           mixL;
AudioMixer4           mixR;
AudioOutputI2S        i2sOut;
AudioControlSGTL5000  sgtl5000;

AudioConnection p01(wav1, 0, mixL, 0);
AudioConnection p02(wav1, 1, mixR, 0);
AudioConnection p03(wav2, 0, mixL, 1);
AudioConnection p04(wav2, 1, mixR, 1);
AudioConnection p05(wav3, 0, mixL, 2);
AudioConnection p06(wav3, 1, mixR, 2);
AudioConnection p07(wav4, 0, mixL, 3);
AudioConnection p08(wav4, 1, mixR, 3);
AudioConnection p09(mixL, 0, i2sOut, 0);
AudioConnection p10(mixR, 0, i2sOut, 1);

// ── 상태 ─────────────────────────────────────────────────────────────
static uint8_t       activeTrack = 0;       // 현재 재생 트랙 (0=없음, 1~4)
static bool          loopMode    = false;
static bool          fadingOut   = false;   // 페이드아웃 진행 중
static unsigned long fadeStart   = 0;
static bool          awaitingVol = false;   // 볼륨 2바이트 프로토콜: 0xF0 다음 바이트 대기
#define FADE_MS      10000UL               // 페이드아웃 시간 (ms)
#define VOL_MARKER   0xF0                  // 볼륨 커맨드 마커 (2바이트 프로토콜)

// ── 내부 유틸 ─────────────────────────────────────────────────────────
static void playRaw(uint8_t track) {
  const char* f = SOUND_FILES[track - 1];
  switch (track) {
    case 1: wav1.play(f); break;
    case 2: wav2.play(f); break;
    case 3: wav3.play(f); break;
    case 4: wav4.play(f); break;
    default: break;
  }
}

static bool trackIsPlaying(uint8_t track) {
  switch (track) {
    case 1: return wav1.isPlaying();
    case 2: return wav2.isPlaying();
    case 3: return wav3.isPlaying();
    case 4: return wav4.isPlaying();
    default: return false;
  }
}

static void stopAll() {
  wav1.stop(); wav2.stop(); wav3.stop(); wav4.stop();
  activeTrack = 0;
  loopMode    = false;
  fadingOut   = false;
  for (uint8_t i = 0; i < 4; i++) {
    mixL.gain(i, 1.0f);
    mixR.gain(i, 1.0f);
  }
  Serial.println("[STOP] all");
}

static void fadeStop() {
  if (activeTrack == 0) return;   // 재생 중이 아니면 무시
  fadingOut = true;
  fadeStart = millis();
  loopMode  = false;              // 루프 재시작 차단
  Serial.println("[FADE] 10s fade-out start");
}

static void startTrack(uint8_t track, bool loop) {
  if (track < 1 || track > SOUND_COUNT) return;
  stopAll();

  const char* file = SOUND_FILES[track - 1];
  if (!SD.exists(file)) {
    Serial.print("[ERR] not found: "); Serial.println(file);
    return;
  }

  playRaw(track);
  activeTrack = track;
  loopMode    = loop;
  Serial.print("[PLAY] "); Serial.print(file);
  Serial.println(loop ? "  (loop)" : "  (once)");
}

// ── 커맨드 처리 ───────────────────────────────────────────────────────
//  0        → stop
//  1~(N*2)  → 홀수=once, 짝수=loop  (track = (cmd+1)/2)
static void handleCmd(uint8_t cmd) {
  Serial.print("[CMD] "); Serial.println(cmd);
  if (cmd == 0) {
    fadeStop();
    return;
  }
  uint8_t maxCmd = SOUND_COUNT * 2;
  if (cmd >= 1 && cmd <= maxCmd) {
    uint8_t track = (cmd + 1) / 2;
    bool    loop  = (cmd % 2 == 0);
    startTrack(track, loop);
  } else {
    Serial.print("[CMD] unknown: "); Serial.println(cmd);
  }
}

// ════════════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 2000) {}

  CMD_SER.begin(CMD_BAUD);

  AudioMemory(AUDIO_MEM);

  sgtl5000.enable();
  sgtl5000.volume(VOLUME);

  // 각 믹서 채널 게인 1.0 (기본)
  for (uint8_t i = 0; i < 4; i++) {
    mixL.gain(i, 1.0f);
    mixR.gain(i, 1.0f);
  }

  if (!SD.begin(SDCS_PIN)) {
    Serial.println("[ERR] SD init failed");
  } else {
    Serial.println("[BOOT] SD OK");
    for (uint8_t i = 0; i < SOUND_COUNT; i++) {
      Serial.print("[SD]  "); Serial.print(SOUND_FILES[i]);
      Serial.println(SD.exists(SOUND_FILES[i]) ? " : OK" : " : MISSING");
    }
  }

  Serial.println("[BOOT] Audio Shield ready");
  Serial.print("[BOOT] CMD_SER Serial1  "); Serial.print(CMD_BAUD); Serial.println(" bps");

  // 상대 MCU의 UART 초기화 시 TX핀 LOW → break condition(0x00) 수신 방지
  delay(200);
  while (CMD_SER.available()) CMD_SER.read();
  Serial.println("[BOOT] CMD_SER buffer flushed");
}

// ════════════════════════════════════════════════════════════════════
void loop() {
  // ── 커맨드 수신 ──────────────────────────────────────────────────
  while (CMD_SER.available()) {
    uint8_t b = (uint8_t)CMD_SER.read();
    if (awaitingVol) {
      awaitingVol = false;
      uint8_t vol = (b > 100) ? 100 : b;
      sgtl5000.volume(vol / 100.0f);
      Serial.print("[VOL] "); Serial.println(vol);
    } else if (b == VOL_MARKER) {
      awaitingVol = true;
    } else {
      handleCmd(b);
    }
  }

  // ── 페이드아웃 처리 ───────────────────────────────────────────────
  if (fadingOut) {
    unsigned long elapsed = millis() - fadeStart;
    if (elapsed >= FADE_MS) {
      stopAll();                  // 페이드 완료 → 하드 스톱 + 게인 복원
      Serial.println("[FADE] done");
    } else {
      float fraction = 1.0f - (float)elapsed / (float)FADE_MS;
      for (uint8_t i = 0; i < 4; i++) {
        mixL.gain(i, fraction);
        mixR.gain(i, fraction);
      }
    }
    return;                       // 페이드 중에는 루프 재생 금지
  }

  // ── 루프 재생: 트랙 종료 시 자동 재시작 ─────────────────────────
  if (loopMode && activeTrack > 0) {
    if (!trackIsPlaying(activeTrack)) {
      playRaw(activeTrack);
    }
  }
}
