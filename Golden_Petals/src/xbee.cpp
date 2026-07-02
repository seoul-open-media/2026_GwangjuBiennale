//  ── 수신 패킷 (PD → Teensy) ────────────────────────────────────────
//  b0  255(start)    b1  robot_id(1~12)  b2  mode(1~2)
//  b3  temp1(10~60)  b4  sust1(0~60)  b5  fan1(0~255)
//  b6  fan_only1     b7  temp2(10~60) b8  sust2(0~60)
//  b9  fan2(0~255)   b10 fan_only2
//  b11 volume(0~100)  0=변경없음
//  b12 loopOn        b13 presetNum    b14 sound  b15 254(end)
#include "xbee.h"
#include "sensors.h"

// {mode, tempTarget, sustainSec, fanSpeed, fanOnly1, tempTarget2, sustainSec2, fanSpeed2, fanOnly2, loopOn, presetNum, sound}
Cmd  cmd     = {1, 45, 5, 200, false, 45, 5, 200, false, false, 0, 0};
bool newCmd  = false;

static uint8_t rxBuf[PKT_LEN];
static uint8_t rxIdx = 0;

void xbeeInit() {
  XBEE.begin(115200);
  AUDIO_SER.setRX(16);   // Serial4 RX = pin 16 (Teensy 4.x 기본값)
  AUDIO_SER.setTX(17);   // Serial4 TX = pin 17
  AUDIO_SER.begin(9600);
}

static void parsePacket() {
  // ── 수신 로우 바이트 덤프
  Serial.print("[XBEE RAW] ");
  for (uint8_t i = 0; i < PKT_LEN; i++) {
    Serial.print(rxBuf[i]); Serial.print(' ');
  }
  Serial.println();

  uint8_t id = rxBuf[1];
  if (id != MY_ROBOT_ID) {
    Serial.print("[XBEE DROP] id mismatch: "); Serial.println(id);
    return;
  }

  uint8_t mode      = rxBuf[2];           // b2  mode
  uint8_t tempTgt   = rxBuf[3];           // b3  temp1  (Unit1)
  uint8_t sustainS  = rxBuf[4];           // b4  sust1  (Unit1)
  uint8_t fanSpd    = rxBuf[5];           // b5  fan1   (Unit1)
  bool    fanOnly1  = (rxBuf[6] == 1);    // b6  fan_only1
  uint8_t tempTgt2  = rxBuf[7];           // b7  temp2  (Unit2)
  uint8_t sustainS2 = rxBuf[8];           // b8  sust2  (Unit2)
  uint8_t fanSpd2   = rxBuf[9];           // b9  fan2   (Unit2)
  bool    fanOnly2  = (rxBuf[10] == 1);   // b10 fan_only2
  bool    loopOn    = (rxBuf[12] == 1);   // b12 loopOn
  uint8_t preset    = rxBuf[13];          // b13 presetNum
  uint8_t sound     = rxBuf[14];          // b14 sound
  uint8_t volume    = rxBuf[11];          // b11 volume 0~100 (0=no change)

  // 범위 검사
  if (mode > 2) {
    Serial.print("[XBEE DROP] mode out of range: "); Serial.println(mode); return;
  }
  if (!fanOnly1 && fanSpd > 0 && tempTgt > 0 && (tempTgt < 10 || tempTgt > 60)) {
    Serial.print("[XBEE DROP] tempTarget1 out of range: "); Serial.println(tempTgt);  return;
  }
  if (!fanOnly2 && fanSpd2 > 0 && tempTgt2 > 0 && (tempTgt2 < 10 || tempTgt2 > 60)) {
    Serial.print("[XBEE DROP] tempTarget2 out of range: "); Serial.println(tempTgt2); return;
  }
  if (sustainS  > 60) {
    Serial.print("[XBEE DROP] sustainSec1 out of range: "); Serial.println(sustainS);  return;
  }
  if (sustainS2 > 60) {
    Serial.print("[XBEE DROP] sustainSec2 out of range: "); Serial.println(sustainS2); return;
  }

  // ── 사운드/볼륨 전용 패킷: cmd 덮어쓰기 없이 오디오실드에만 전달
  // build_sound_only/build_volume_only 설계: mode=2, preset=0, 모션 파라미터 전부 0
  if (mode == 2 && preset == 0 &&
      tempTgt == 0 && sustainS == 0 && fanSpd == 0 &&
      tempTgt2 == 0 && sustainS2 == 0 && fanSpd2 == 0) {
    if (volume > 0) {
      AUDIO_SER.write((uint8_t)0xF0);   // 볼륨 마커
      AUDIO_SER.write(volume);
      Serial.print("[VOL] "); Serial.println(volume);
    } else if (sound == 0) {
      AUDIO_SER.write((uint8_t)0);      // STOP
      Serial.println("[SOUND] STOP");
    } else {
      // 오디오실드 커맨드: 홀수=한번, 짝수=반복
      // audioCmd = (track-1)*2 + 1 + loopOn
      uint8_t audioCmd = (uint8_t)((sound - 1) * 2 + 1 + (loopOn ? 1 : 0));
      AUDIO_SER.write(audioCmd);
      Serial.print("[SOUND] track="); Serial.print(sound);
      Serial.print(" loop="); Serial.print(loopOn);
      Serial.print(" cmd="); Serial.println(audioCmd);
    }
    return;  // cmd·newCmd 수정 없음 — 루프 상태 보존
  }

  cmd    = {mode, tempTgt, sustainS, fanSpd, fanOnly1, tempTgt2, sustainS2, fanSpd2, fanOnly2, loopOn, preset, sound};
  newCmd = true;

  Serial.print("[CMD] id=");   Serial.print(id);
  Serial.print(" t1=");        Serial.print(tempTgt);
  Serial.print(" s1=");        Serial.print(sustainS);
  Serial.print(" f1=");        Serial.print(fanSpd);
  Serial.print(" fo1=");       Serial.print(fanOnly1);
  Serial.print(" t2=");        Serial.print(tempTgt2);
  Serial.print(" s2=");        Serial.print(sustainS2);
  Serial.print(" f2=");        Serial.print(fanSpd2);
  Serial.print(" fo2=");       Serial.print(fanOnly2);
  Serial.print(" loop=");      Serial.print(loopOn);
  Serial.print(" sound=");     Serial.println(sound);
}

void readXBee() {
  while (XBEE.available()) {
    uint8_t b = XBEE.read();
    if (rxIdx == 0) {
      if (b == START_B) rxBuf[rxIdx++] = b;
    } else {
      rxBuf[rxIdx++] = b;
      if (rxIdx == PKT_LEN) {
        if (rxBuf[PKT_LEN - 1] == END_B) parsePacket();
        else { Serial.print("[XBEE] bad end byte: "); Serial.println(rxBuf[PKT_LEN-1]); }
        rxIdx = 0;
      }
    }
  }
}

// ── Teensy → PD 상태 패킷 송신 (21바이트, IF와 동일 포맷) ─────────────
// b0=255  b1=robot_id+50  b2=fault  b3=faultCode
// b4=temp1 MSB  b5=temp1 LSB   → (base-253, ×100)
// b6=temp2 MSB  b7=temp2 LSB
// b8=state1
// b9=elapsed1 MSB  b10=elapsed1 LSB  (초, base-253)
// b11=state2       ← GP: Unit2 state  (IF: switch)
// b12=elapsed2 MSB  b13=elapsed2 LSB  ← GP: Unit2 elapsed  (IF: swElapsed)
// b14=amb MSB  b15=amb LSB  (base-253, ×100)
// b16=pkt_num MSB  b17=pkt_num LSB  (base-253)
// b18=tempTarget1  b19=tempTarget2   ← GP: Unit2 목표온도  (IF: targetTempMin)
// b20=254(END)
static uint16_t pktNum = 0;
void sendStatus(State state1, unsigned long elapsedMs1,
                State state2, unsigned long elapsedMs2,
                bool fault, uint8_t faultCode,
                float temp1, float temp2,
                uint8_t sw, unsigned long swElapsedMs) {
  (void)sw; (void)swElapsedMs;  // GP: b11-13에 state2/elapsed2 사용
  const uint16_t BMAX = 252 * 253 + 252;  // 64008
  uint16_t t1   = (uint16_t)constrain((int)(temp1 * 100.0f), 0, (int)BMAX);
  uint16_t t2   = (uint16_t)constrain((int)(temp2 * 100.0f), 0, (int)BMAX);
  uint16_t el1  = (uint16_t)constrain((long)(elapsedMs1 / 1000UL), 0, (long)BMAX);
  uint16_t el2  = (uint16_t)constrain((long)(elapsedMs2 / 1000UL), 0, (long)BMAX);
  uint16_t amb  = (uint16_t)constrain((int)(((ambTemp + ambTemp2) / 2.0f) * 100.0f), 0, (int)BMAX);

  uint8_t pkt[STATUS_PKT_LEN] = {
    START_B,
    MY_ROBOT_ID + 50,
    fault ? 1 : 0,
    faultCode,
    (uint8_t)(t1 / 253),          // b4  temp1 MSB  (base-253, ×100)
    (uint8_t)(t1 % 253),          // b5  temp1 LSB
    (uint8_t)(t2 / 253),          // b6  temp2 MSB
    (uint8_t)(t2 % 253),          // b7  temp2 LSB
    (uint8_t)state1,              // b8  state1
    (uint8_t)(el1 / 253),         // b9  elapsed1 MSB (초)
    (uint8_t)(el1 % 253),         // b10 elapsed1 LSB
    (uint8_t)state2,              // b11 state2  (GP: Unit2 state)
    (uint8_t)(el2 / 253),         // b12 elapsed2 MSB (초)
    (uint8_t)(el2 % 253),         // b13 elapsed2 LSB
    (uint8_t)(amb / 253),         // b14 amb MSB  (base-253, ×100)
    (uint8_t)(amb % 253),         // b15 amb LSB
    (uint8_t)(pktNum / 253),      // b16 pkt_num MSB
    (uint8_t)(pktNum % 253),      // b17 pkt_num LSB
    cmd.tempTarget,               // b18 Unit1 목표 가열 온도
    cmd.tempTarget2,              // b19 Unit2 목표 가열 온도  (GP) / targetTempMin (IF)
    cmd.mode,                     // b20 모드 (1=user_defined 2=play_preset)
    cmd.presetNum,                // b21 프리셋 번호
    END_B                         // b22
  };
  XBEE.write(pkt, STATUS_PKT_LEN);
  pktNum++;
  if (pktNum > 252*253+252) pktNum = 0;
}
