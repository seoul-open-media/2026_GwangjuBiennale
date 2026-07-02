// sensor_test — MLX90614 x2 + SSD1306 OLED 최소 테스트
// Teensy 4.0
//
// Wire  (I2C1)  SDA=18  SCL=19  → MLX1  0x5A
// Wire2 (I2C2)  SDA=24  SCL=25  → MLX2  0x5A
//                                → OLED  0x3C

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MLX90614.h>
#include <Adafruit_SSD1306.h>

static Adafruit_MLX90614 mlx1;
static Adafruit_MLX90614 mlx2;
static Adafruit_SSD1306  oled(128, 64, &Wire2, -1);

static bool ok1, ok2, oledOk;

void setup() {
  Serial.begin(115200);

  Wire.setSDA(18);  Wire.setSCL(19);
  Wire2.setSDA(24); Wire2.setSCL(25);
  Wire.begin();
  Wire2.begin();
  Wire.setClock(100000);   // MLX1
  Wire2.setClock(25000);   // MLX2 + OLED (노이즈 대응)

  ok1    = mlx1.begin(0x5A, &Wire);
  ok2    = mlx2.begin(0x5A, &Wire2);
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);

  Serial.print("MLX1: "); Serial.println(ok1 ? "OK" : "NG");
  Serial.print("MLX2: "); Serial.println(ok2 ? "OK" : "NG");
  Serial.print("OLED: "); Serial.println(oledOk ? "OK" : "NG");

  if (oledOk) {
    oled.clearDisplay();
    oled.setTextColor(SSD1306_WHITE);
    oled.setTextSize(1);
    oled.setCursor(0, 0);
    oled.println("Sensor Test");
    oled.print("MLX1: "); oled.println(ok1 ? "OK" : "NG");
    oled.print("MLX2: "); oled.println(ok2 ? "OK" : "NG");
    oled.display();
    delay(1500);
  }
}

void loop() {
  float t1 = ok1 ? mlx1.readObjectTempC()  : -99.0f;
  float t2 = ok2 ? mlx2.readObjectTempC()  : -99.0f;
  float a1 = ok1 ? mlx1.readAmbientTempC() : -99.0f;
  float a2 = ok2 ? mlx2.readAmbientTempC() : -99.0f;

  // 시리얼
  Serial.print("S1="); Serial.print(t1, 1);
  Serial.print(" S2="); Serial.print(t2, 1);
  Serial.print(" A1="); Serial.print(a1, 1);
  Serial.print(" A2="); Serial.println(a2, 1);

  // OLED
  if (oledOk) {
    oled.clearDisplay();
    oled.setTextColor(SSD1306_WHITE);

    oled.setTextSize(1);
    oled.setCursor(0, 0);
    oled.println("-- Sensor Test --");

    oled.setTextSize(2);
    oled.setCursor(0, 12);
    if (ok1) {
      oled.print("S1:");
      oled.print(t1, 1);
      oled.println("C");
    } else {
      oled.println("S1: --NG--");
    }

    oled.setCursor(0, 34);
    if (ok2) {
      oled.print("S2:");
      oled.print(t2, 1);
      oled.println("C");
    } else {
      oled.println("S2: --NG--");
    }

    oled.setTextSize(1);
    oled.setCursor(0, 56);
    oled.print("amb ");
    oled.print(a1, 1); oled.print("/");
    oled.print(a2, 1); oled.print("C");

    oled.display();
  }

  delay(500);
}
