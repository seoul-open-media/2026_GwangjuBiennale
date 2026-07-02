#pragma once
// ════════════════════════════════════════════════════════════════════
//  config.h  —  Hidden Sound Path
//  Teensy 4.0 + Teensy Audio Shield (SGTL5000)
//  전원 인가 즉시 SOUND1.WAV 무한 루프 재생
// ════════════════════════════════════════════════════════════════════

#define SDCS_PIN    10           // SD CS 핀 (Audio Shield 기본값)
#define VOLUME      1.0f         // 헤드폰 술력 볼륨 (0.0 ~ 1.0)
#define DAC_VOLUME  1.0f         // DAC 볼륨 (0.0 ~ 1.0)
#define AUDIO_MEM   24           // AudioMemory 블록 수

#define SOUND_FILE  "SOUND1.WAV" // 루프 재생할 WAV 파일
