#pragma once
// ════════════════════════════════════════════════════════════════════
//  config.h  —  Audio Shield Controller
//  Teensy 4.0 + Teensy Audio Shield (SGTL5000)
// ════════════════════════════════════════════════════════════════════

// ── 수신 시리얼 (로봇 Teensy Serial2 TX(pin8) → Audio Teensy RX) ─────
#define CMD_SER     Serial1      // RX = pin 0
#define CMD_BAUD    9600

// ── Audio Shield ─────────────────────────────────────────────────────
#define SDCS_PIN    10           // SD CS 핀 (Audio Shield 기본값)
#define VOLUME      0.7f         // 출력 볼륨 (0.0 ~ 1.0)
#define AUDIO_MEM   24           // AudioMemory 블록 수
#define SOUND_COUNT 4            // 사용할 사운드 파일 수 (최대 4)

// ── SD 파일명 ────────────────────────────────────────────────────────
//  SD 루트에 아래 파일을 배치할 것 (44100Hz, 16-bit, WAV)
//  SOUND1.WAV  SOUND2.WAV  SOUND3.WAV  SOUND4.WAV

// ── 사운드 커맨드 정의 ────────────────────────────────────────────────
//  0 : stop          — 모든 재생 정지
//  1 : sound1 once   — SOUND1.WAV 1회 재생
//  2 : sound1 loop   — SOUND1.WAV 반복 재생
//  3 : sound2 once   — SOUND2.WAV 1회 재생
//  4 : sound2 loop   — SOUND2.WAV 반복 재생
//  5 : sound3 once   — SOUND3.WAV 1회 재생
//  6 : sound3 loop   — SOUND3.WAV 반복 재생
//  7 : sound4 once   — SOUND4.WAV 1회 재생
//  8 : sound4 loop   — SOUND4.WAV 반복 재생
//
//  규칙: 홀수 cmd → once, 짝수 cmd → loop
//        track = (cmd + 1) / 2
