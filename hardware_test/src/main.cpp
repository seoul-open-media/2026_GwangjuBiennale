// ════════════════════════════════════════════════════════════════════
//  main.cpp  —  하드웨어 테스트
//  Hardware Test
//  Teensy 4.0  /  SMA Controller v1.6
//
//  [루프]
//    · MOSFET 6채널 3초 주기 ON/OFF (100% 듀티, 비차단)
//    · MLX90614 ×2 (Wire / Wire2) 온도를 100ms 마다 읽기
//    · SSD1306 OLED에 MOSFET 상태 + 온도 + XBee 통합 표시
//    · XBee (Serial1) — 수신 패킷 OLED 표시 후 코디네이터에게 에코
//
//  핀 배치 (SMA Controller v1.6)
//    MOSFET1 SMA_A1 : pin 2
//    MOSFET2 SMA_A2 : pin 3
//    MOSFET3 FAN1   : pin 4
//    MOSFET4 SMA_B1 : pin 5
//    MOSFET5 SMA_B2 : pin 6
//    MOSFET6 FAN2   : pin 7
//    MLX1    Wire   : SDA=18 SCL=19
//    MLX2    Wire2  : SDA=24 SCL=25
//    OLED    Wire3  : SCL=42 SDA=43  0x3C
//    XBee    Serial1: TX=1  RX=0  115200bps
// ════════════════════════════════════════════════════════════════════
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MLX90614.h>
// #include <Adafruit_GFX.h>
// #include <Adafruit_SSD1306.h>

// ── 핀 ──────────────────────────────────────────────────────────────
#define SMA_A1_PIN  2
#define SMA_A2_PIN  3
#define FAN1_PIN    4
#define SMA_B1_PIN  5
#define SMA_B2_PIN  6
#define FAN2_PIN    7

static const uint8_t MOSFET_PINS[] = {
  SMA_A1_PIN, SMA_A2_PIN, FAN1_PIN,
  SMA_B1_PIN, SMA_B2_PIN, FAN2_PIN
};
static const uint8_t MOSFET_COUNT = sizeof(MOSFET_PINS);

// ── XBee ─────────────────────────────────────────────────────────────
#define XBEE       Serial1

// ── 상수 ────────────────────────────────────────────────────────────
#define BLINK_PERIOD_MS  3000u   // ON↔OFF 토글 주기(ms)

// ── 디바이스 ────────────────────────────────────────────────────────
static Adafruit_MLX90614 mlx1;
static Adafruit_MLX90614 mlx2;
// static Adafruit_SSD1306  display(128, 64, &Wire3, -1);  // OLED 배선 확인 후 활성화

static bool mlx1Ok = false;
static bool mlx2Ok = false;
// static bool oledOk = false;

// ── XBee 수신 버퍼 ──────────────────────────────────────────────────
static uint32_t rxByteCount = 0;   // 수신 바이트 카운터

// ── XBee 처리: 수신 바이트 → Serial 출력 + 에코 ─────────────────────
static void processXBee() {
  while (XBEE.available()) {
    uint8_t b = XBEE.read();
    rxByteCount++;

    // Serial 출력 (hex + dec)
    Serial.print("[XBEE #"); Serial.print(rxByteCount);
    Serial.print("] 0x"); Serial.print(b, HEX);
    Serial.print(" (");    Serial.print(b, DEC);
    Serial.println(")");

    // 코디네이터에게 바이트 그대로 에코
    XBEE.write(b);
  }
}

// ── OLED 헬퍼 — 배선 확인 후 주석 해제 ────────────────────────────
// static void oledDraw(bool mosfetOn, float obj1, float obj2, float amb1, float amb2) {
//   display.clearDisplay();
//   display.setTextColor(SSD1306_WHITE);
//   display.setTextSize(1);
//   display.setCursor(0, 0);
//   display.print("MOS:"); display.print(mosfetOn ? "ON " : "OFF");
//   display.print(" t="); display.print(millis() / 1000UL); display.println("s");
//   display.drawFastHLine(0, 9, 128, SSD1306_WHITE);
//   display.setCursor(0, 11);
//   if (mlx1Ok) { display.print("S1 Obj:"); display.print(obj1,1); display.print(" A:"); display.print(amb1,1); display.println("C"); }
//   else { display.println("S1: NOT FOUND"); }
//   display.setCursor(0, 20);
//   if (mlx2Ok) { display.print("S2 Obj:"); display.print(obj2,1); display.print(" A:"); display.print(amb2,1); display.println("C"); }
//   else { display.println("S2: NOT FOUND"); }
//   display.drawFastHLine(0, 29, 128, SSD1306_WHITE);
//   display.setCursor(0, 31);
//   display.print("XB RX:"); display.print(rxCount); display.print(" ECHO:"); display.println(echoCount);
//   display.setCursor(0, 40); display.println(lastPktStr);
//   display.display();
// }

// ════════════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 2000) {}

  // ─ MOSFET 핀 초기화 (I2C 이전에 모두 LOW로 설정)
  for (uint8_t i = 0; i < MOSFET_COUNT; i++) {
    pinMode(MOSFET_PINS[i], OUTPUT);
    digitalWrite(MOSFET_PINS[i], LOW);
  }
  // PWM 주파수 설정
  for (uint8_t i = 0; i < MOSFET_COUNT; i++) {
    analogWriteFrequency(MOSFET_PINS[i], 10000u);
    analogWrite(MOSFET_PINS[i], 0);
  }

  // ─ XBee 초기화
  XBEE.begin(115200);

  // ─ I2C 초기화
#if defined(CORE_TEENSY)
  Wire.setSDA(18);  Wire.setSCL(19);
  Wire2.setSDA(24); Wire2.setSCL(25);
  // Wire3.setSDA(43); Wire3.setSCL(42);  // OLED 배선 확인 후 활성화
#endif
  Wire.begin();   Wire.setClock(100000);
  Wire2.begin();  Wire2.setClock(50000);
  // Wire3.begin();  Wire3.setClock(400000);
  delay(50);

  // ─ OLED 초기화 — 배선 확인 후 주석 해제
  // oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  // if (!oledOk) oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3D);
  // if (oledOk) {
  //   display.clearDisplay(); display.setTextSize(1);
  //   display.setTextColor(SSD1306_WHITE); display.setCursor(0, 0);
  //   display.println("HW Test v1.6"); display.println("Initializing...");
  //   display.display();
  // }
  Serial.println("[BOOT] HW Test v1.6 — MOSFET+MLX+XBee");

  // ─ 온도 센서 초기화
  mlx1Ok = mlx1.begin(0x5A, &Wire);
  mlx2Ok = mlx2.begin(0x5A, &Wire2);
  Serial.print("[MLX1] "); Serial.println(mlx1Ok ? "OK" : "NOT FOUND");
  Serial.print("[MLX2] "); Serial.println(mlx2Ok ? "OK" : "NOT FOUND");

  Serial.println("[LOOP] MOSFET blink + temp + XBee echo starting...");
}

// ════════════════════════════════════════════════════════════════════
void loop() {
  static unsigned long lastTemp   = 0;
  static unsigned long lastBlink  = 0;
  static float obj1 = 0, obj2 = 0, amb1 = 0, amb2 = 0;
  static bool  mosfetOn = false;

  unsigned long now = millis();

  // ─ XBee 수신 처리 (매 루프)
  processXBee();

  // ─ BLINK_PERIOD_MS 마다 MOSFET 토글
  if (now - lastBlink >= BLINK_PERIOD_MS) {
    lastBlink = now;
    mosfetOn = !mosfetOn;
    for (uint8_t i = 0; i < MOSFET_COUNT; i++)
      analogWrite(MOSFET_PINS[i], mosfetOn ? 255 : 0);
    Serial.print("[MOS] "); Serial.println(mosfetOn ? "ON" : "OFF");
  }

  // ─ 100ms 마다 온도 읽기 + OLED 갱신
  if (now - lastTemp >= 100) {
    lastTemp = now;

    if (mlx1Ok) {
      float o = mlx1.readObjectTempC();
      float a = mlx1.readAmbientTempC();
      if (!isnan(o) && o > -10.0f && o < 120.0f) obj1 = o;
      if (!isnan(a) && a > -10.0f && a < 80.0f)  amb1 = a;
    }
    if (mlx2Ok) {
      float o = mlx2.readObjectTempC();
      float a = mlx2.readAmbientTempC();
      if (!isnan(o) && o > -10.0f && o < 120.0f) obj2 = o;
      if (!isnan(a) && a > -10.0f && a < 80.0f)  amb2 = a;
    }

    Serial.print("[TEMP] MLX1 obj="); Serial.print(obj1, 1);
    Serial.print(" amb=");            Serial.print(amb1, 1);
    Serial.print("  MLX2 obj=");      Serial.print(obj2, 1);
    Serial.print(" amb=");            Serial.println(amb2, 1);

    // if (oledOk) oledDraw(mosfetOn, obj1, obj2, amb1, amb2);
  }
}

