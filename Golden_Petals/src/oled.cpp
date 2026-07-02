// oled.cpp  —  SSD1306 128x64 디스플레이 (Wire2)
#include "oled.h"
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "config.h"
#include "sensors.h"
#include "output.h"
#include "xbee.h"

extern State state1;
extern State state2;

// clkDuring=400kHz(OLED 전송 속도), clkAfter=50kHz(MLX2 복원)
static Adafruit_SSD1306 display(128, 64, &Wire2, -1, 400000UL, 50000UL);

void oledInit() {
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C))
    display.begin(SSD1306_SWITCHCAPVCC, 0x3D);
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(2);
  display.setCursor(0, 0);
  display.print("GP  R"); display.println(MY_ROBOT_ID);
  display.setTextSize(1);
  display.println("Golden Petal");
  display.println("Initializing...");
  display.display();
}

void oledError(uint8_t sensorId, uint8_t faultCode) {
  static bool blink = false;
  blink = !blink;

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  // 상단: 굵은 경고
  display.setTextSize(2);
  display.setCursor(0, 0);
  display.println(blink ? "!! ERROR !!" : "           ");

  display.setTextSize(1);
  display.setCursor(0, 20);
  display.println("SENSOR FAILURE");

  display.setCursor(0, 30);
  if (sensorId == 1)      display.println("MLX1 (Wire)  FAIL");
  else if (sensorId == 2) display.println("MLX2 (Wire2) FAIL");
  else                    display.println("MLX1 & MLX2  FAIL");

  display.setCursor(0, 40);
  display.print("code="); display.print(faultCode);

  display.setCursor(0, 50);
  display.println("ALL OUTPUTS OFF");

  display.setCursor(0, 58);
  display.println("Check sensor/wiring");

  display.display();
}

void oledUpdate(unsigned long stateMs) {
  unsigned long elapsed = (millis() - stateMs) / 1000UL;
  const char* st1Name =
    state1 == IDLE         ? "IDLE" :
    state1 == HEATING      ? "HEAT" :
    state1 == SUSTAINING   ? "SUST" :
    state1 == COOLING      ? "COOL" : "ERR!";
  const char* st2Name =
    state2 == IDLE         ? "IDLE" :
    state2 == HEATING      ? "HEAT" :
    state2 == SUSTAINING   ? "SUST" :
    state2 == COOLING      ? "COOL" : "ERR!";

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  // 0: 로봇 ID / Unit1 상태 / Unit2 상태
  display.setCursor(0, 0);
  display.print("R"); display.print(MY_ROBOT_ID);
  display.print(" U1:"); display.print(st1Name);
  display.print(" U2:"); display.print(st2Name);

  // 1: S1 온도 / 목표
  display.setCursor(0, 11);
  display.print("S1:"); display.print(objTemp1, 1);
  display.print("C TG:"); display.print(cmd.tempTarget); display.print("C");

  // 2: S2 온도 / 목표
  display.setCursor(0, 21);
  display.print("S2:"); display.print(objTemp2, 1);
  display.print("C TG:"); display.print(cmd.tempTarget2); display.print("C");

  // 3: 실온 / 경과
  display.setCursor(0, 31);
  display.print("AM:"); display.print(ambTemp, 1);
  display.print("C T:"); display.print(elapsed); display.print("s");

  // 4: SMA / FAN 상태
  display.setCursor(0, 41);
  display.print(sma1Active ? "[U1 SMA:ON ] " : "[U1 SMA:OFF] ");
  display.print(sma2Active ? "[U2:ON]" : "[U2:OFF]");

  // 5: Unit1 SUSTAINING 진행 표시
  if (state1 == SUSTAINING) {
    display.setCursor(0, 51);
    display.print("U1 SUST ");
    display.print(elapsed); display.print("/");
    display.print(cmd.sustainSec); display.print("s");
  }

  display.display();
}
