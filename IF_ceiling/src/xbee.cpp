// ── CMD 패킷 구조 (PD → Teensy, 16바이트) ───────────────────────────
//  b0  255 (START)
//  b1  robot_id        (MY_ROBOT_ID)
//  b2  mode            1=user_defined  2=play_preset
//  b3  tempTarget      40~50 °C  (목표 가열 온도, user_defined)
//  b4  sustainSec      0~60 s    (목표 도달 후 유지 시간)
//  b5  fanSpeed        0~255     (팬 PWM)
//  b6  fanOnly         0/1       (1=팬만 ON, SMA 없음)
//  b7  targetTempMin   10~30 °C  (쿨링 저점 온도, 0=미설정→ambTemp+0.5 기준)
//  b8  heatPwm         0~255     (SMA 가열 PWM, 0=기본값)
//  b9~b10  reserved    (0)
//  b11 volume          0~100     (오디오 볼륨, 0=변경 없음)
//  b12 loopOn          0/1       (루프 반복 여부)
//  b13 presetNum       (프리셋 번호)
//  b14 sound           0~4       (사운드 커맨드, b11==0일 때만 유효)
//  b15 254 (END)
#include "xbee.h"
#include "sensors.h"

Cmd  cmd     = {1, 45, 5, 200, false, 0, 0, 0, 0};
bool newCmd  = false;

static uint8_t rxBuf[PKT_LEN];
static uint8_t rxIdx = 0;

void xbeeInit() {
  XBEE.begin(115200);
  AUDIO_SER.begin(9600);
}

static void parsePacket() {
  uint8_t id = rxBuf[1];
  // 다른 로봇의 STATUS 패킷(id = robot_id+50) — 조용히 무시
  if (id >= 50) return;
  if (id != MY_ROBOT_ID) {
    Serial.print("[XBEE DROP] id mismatch: "); Serial.println(id);
    return;
  }

  // ── 수신 로우 바이트 덤프 (내 CMD 패킷만)
  Serial.print("[XBEE RAW] ");
  for (uint8_t i = 0; i < PKT_LEN; i++) {
    Serial.print(rxBuf[i]); Serial.print(' ');
  }
  Serial.println();

  uint8_t mode     = rxBuf[2];
  uint8_t tempTgt  = rxBuf[3];
  uint8_t sustainS = rxBuf[4];
  uint8_t fanSpd   = rxBuf[5];
  bool    fanOnly  = (rxBuf[6] == 1);  // b6: 팬 전용
  uint8_t tempMin  = rxBuf[7];         // b7: targetTempMin 10~30°C (0=미설정)
  uint8_t heatPwm  = rxBuf[8];         // b8: SMA 가열 PWM (0=기본값)
  bool    loopOn   = (rxBuf[12] == 1);
  uint8_t preset   = rxBuf[13];
  uint8_t sound    = rxBuf[14];
  uint8_t volume   = rxBuf[11];   // b11: 0=no change, 1~100

  if (mode > 2) {
    Serial.print("[XBEE DROP] mode out of range: "); Serial.println(mode);
    return;
  }
  bool isResetCmd = (!fanOnly && fanSpd == 0 && tempTgt == 0);
  if (mode == 1 && !fanOnly && !isResetCmd &&
      (tempTgt < TEMP_TARGET_MIN || tempTgt > TEMP_TARGET_MAX)) {
    Serial.print("[XBEE DROP] tempTarget out of range (40~50): "); Serial.println(tempTgt);
    return;
  }
  if (tempMin > 0 && (tempMin < 10 || tempMin > 30)) {
    Serial.print("[XBEE DROP] targetTempMin out of range: "); Serial.println(tempMin);
    return;
  }
  if (sustainS > 60) {
    Serial.print("[XBEE DROP] sustainSec out of range: "); Serial.println(sustainS);
    return;
  }

  // ── 사운드/볼륨 전용 패킷: cmd 덮어쓰기 없이 오디오실드에만 전달
  // build_sound_only/build_volume_only 설계: mode=2, preset=0, 모션 파라미터 전부 0
  if (mode == 2 && preset == 0 &&
      tempTgt == 0 && sustainS == 0 && fanSpd == 0 && tempMin == 0) {
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

  cmd.mode          = mode;
  cmd.tempTarget    = tempTgt;
  cmd.sustainSec    = sustainS;
  cmd.fanSpeed      = fanSpd;
  cmd.fanOnly       = fanOnly;
  cmd.targetTempMin = tempMin;
  cmd.heatPwm       = heatPwm;
  cmd.loopOn        = loopOn;
  cmd.presetNum     = preset;
  cmd.sound         = sound;
  newCmd = true;

  Serial.print("[CMD] id=");      Serial.print(id);
  Serial.print(" mode=");         Serial.print(mode);
  Serial.print(" temp=");         Serial.print(tempTgt);
  Serial.print(" sust=");         Serial.print(sustainS);
  Serial.print(" fan=");          Serial.print(fanSpd);
  Serial.print(" pwm=");          Serial.print(heatPwm == 0 ? SMA_PWM_MAX : min((int)heatPwm, (int)SMA_PWM_MAX));
  Serial.print(" tMin=");         Serial.print(tempMin);
  Serial.print(" loop=");         Serial.print(loopOn);
  Serial.print(" sound=");        Serial.println(sound);
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

// ── Teensy → PD 상태 패킷 송신 ──────────────────────────────────────
// b0=255  b1=robot_id  b2=fault  b3=faultCode
// 인코딩: base-253 (각 바이트 0~252 → 253/254/255 절대 미출현)
// PD 복원: value = MSB*253 + LSB
// 온도: temp*100 / 경과시간: 초 단위
// b14=amb MSB  b15=amb LSB  b16=pkt_num MSB  b17=pkt_num LSB
// b18=tempTarget(40~50, user_defined)  b19=targetTempMin  b20=254(END)
static uint16_t pktNum = 0;  // 전송 순서 카운터 (0~64008 wraparound)
void sendStatus(State currentState, unsigned long elapsedMs,
                bool fault, uint8_t faultCode,
                float temp1, float temp2,
                uint8_t sw, unsigned long swElapsedMs) {
  const uint16_t BMAX = 252 * 253 + 252;  // 64008
  uint16_t t1   = (uint16_t)constrain((int)(temp1 * 100.0f), 0, (int)BMAX);
  uint16_t t2   = (uint16_t)constrain((int)(temp2 * 100.0f), 0, (int)BMAX);
  uint16_t el   = (uint16_t)constrain((long)(elapsedMs   / 1000UL), 0, (long)BMAX);
  uint16_t swEl = (uint16_t)constrain((long)(swElapsedMs / 1000UL), 0, (long)BMAX);
  uint16_t amb  = (uint16_t)constrain((int)(((ambTemp + ambTemp2) / 2.0f) * 100.0f), 0, (int)BMAX);

  uint8_t pkt[STATUS_PKT_LEN] = {
    START_B,
    MY_ROBOT_ID + 50,
    fault ? 1 : 0,
    faultCode,
    (uint8_t)(t1 / 253),          // b4  temp1 MSB
    (uint8_t)(t1 % 253),          // b5  temp1 LSB
    (uint8_t)(t2 / 253),          // b6  temp2 MSB
    (uint8_t)(t2 % 253),          // b7  temp2 LSB
    (uint8_t)currentState,        // b8  state
    (uint8_t)(el / 253),          // b9  elapsed MSB
    (uint8_t)(el % 253),          // b10 elapsed LSB
    sw,                           // b11 switch
    (uint8_t)(swEl / 253),        // b12 sw elapsed MSB
    (uint8_t)(swEl % 253),        // b13 sw elapsed LSB
    (uint8_t)(amb / 253),         // b14 amb MSB  (amb*100, base-253)
    (uint8_t)(amb % 253),         // b15 amb LSB
    (uint8_t)(pktNum / 253),      // b16 pkt_num MSB
    (uint8_t)(pktNum % 253),      // b17 pkt_num LSB
    cmd.tempTarget,               // b18 목표 가열 온도 (40~50°C, user_defined)
    cmd.targetTempMin,            // b19 쿨링 저점 온도 (10~30°C, 0=미설정)
    cmd.mode,                     // b20 모드 (1=user_defined 2=play_preset)
    cmd.presetNum,                // b21 프리셋 번호
    END_B                         // b22
  };
  XBEE.write(pkt, STATUS_PKT_LEN);



  pktNum++;
  if (pktNum > 252*253+252) pktNum = 0;  // BMAX wraparound
}
