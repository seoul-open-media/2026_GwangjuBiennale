// oled.cpp  —  SSD1306 128x64 디스플레이 (Wire2)
#include "oled.h"
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "config.h"
#include "sensors.h"
#include "output.h"
#include "xbee.h"

extern State state;
extern unsigned long coolAmbMs;

// clkDuring=400kHz(OLED 전송 속도), clkAfter=50kHz(MLX2 복원)
static Adafruit_SSD1306 display(128, 64, &Wire2, -1, 400000UL, 50000UL);

void oledInit() {
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C))
    display.begin(SSD1306_SWITCHCAPVCC, 0x3D);
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(2);
  display.setCursor(0, 0);
  display.print("IF  R"); display.println(MY_ROBOT_ID);
  display.setTextSize(1);
  display.println("Indet. Floating");
  display.println("Initializing...");
  display.display();
}

void oledError(uint8_t sensorId) {
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

  display.setCursor(0, 32);
  if (sensorId == 1)      display.println("MLX1 (Wire)  FAIL");
  else if (sensorId == 2) display.println("MLX2 (Wire2) FAIL");
  else                    display.println("MLX1 & MLX2  FAIL");

  display.setCursor(0, 44);
  display.println("ALL OUTPUTS OFF");

  display.setCursor(0, 54);
  display.println("Check sensor / wiring");

  display.display();
}

void oledUpdate(unsigned long stateMs) {
  unsigned long elapsed = (millis() - stateMs) / 1000UL;
  const char* stName =
    state == IDLE         ? "IDLE" :
    state == HEATING      ? "HEAT" :
    state == SUSTAINING   ? "SUST" :
    state == COOLING      ? "COOL" : "ERR!";

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  // 0: 로봇 ID / 상태 / 경과 시간
  display.setCursor(0, 0);
  display.print("R"); display.print(MY_ROBOT_ID);
  display.print(" "); display.print(stName);
  display.print(" T:"); display.print(elapsed); display.print("s");
  display.print(" LP:"); display.print(cmd.loopOn ? "Y" : "N");

  // 1: S1 온도 / 목표
  display.setCursor(0, 11);
  display.print("S1:"); display.print(objTemp1, 1);
  display.print("C TG:"); display.print(cmd.tempTarget); display.print("C");

  // 2: S2 온도 / 실온
  display.setCursor(0, 21);
  display.print("S2:"); display.print(objTemp2, 1);
  display.print("C AM:"); display.print(ambTemp, 1); display.print("C");

  // 3: 유지시간 / 팬속도
  display.setCursor(0, 31);
  display.print("SUS:"); display.print(cmd.sustainSec);
  display.print("s FAN:"); display.print(cmd.fanSpeed);

  // 4: SMA / FAN 상태
  display.setCursor(0, 41);
  display.print(smaActive ? "[SMA:ON ] " : "[SMA:OFF] ");
  display.print(fanActive ? "[FAN:ON]"  : "[FAN:OFF]");

  // 5: 상태 세부 진행 표시
  display.setCursor(0, 51);
  if (state == SUSTAINING) {
    display.print("SUST ");
    display.print(elapsed); display.print("/");
    display.print(cmd.sustainSec); display.print("s");
  } else if (state == COOLING && coolAmbMs > 0) {
    uint32_t passed = (millis() - coolAmbMs) / 1000UL;
    uint32_t remain = 60 > passed ? 60 - passed : 0;
    display.print("AMB! extra:"); display.print(remain); display.print("s");
  }

  display.display();
}
