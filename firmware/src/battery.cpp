#include "battery.h"

#include <Arduino.h>

#include "board.h"

namespace battery {
namespace {

constexpr uint32_t kSampleMs = 5000;
float smoothed = 0;
uint32_t lastSample = 0;

// Typical Li-ion resting curve (volts -> percent), linearly interpolated.
const float kCurve[][2] = {{3.30f, 0},  {3.50f, 8},  {3.60f, 18}, {3.70f, 35}, {3.80f, 52},
                           {3.90f, 66}, {4.00f, 79}, {4.10f, 90}, {4.20f, 100}};

float readVolts() {
  uint32_t sum = 0;
  for (int i = 0; i < 8; ++i) sum += analogReadMilliVolts(PIN_BATTERY_ADC);
  return (sum / 8.0f) * BATTERY_DIVIDER / 1000.0f / BATTERY_ADC_TRIM;
}

}  // namespace

void begin() {
  analogSetPinAttenuation(PIN_BATTERY_ADC, ADC_11db);
  smoothed = readVolts();
  lastSample = millis();
}

void update() {
  if (millis() - lastSample < kSampleMs) return;
  lastSample = millis();
  float v = readVolts();
  smoothed = smoothed <= 0 ? v : smoothed * 0.7f + v * 0.3f;
}

float volts() { return smoothed; }

bool present() { return smoothed > 2.5f; }

int percent() {
  if (!present()) return -1;
  const int n = sizeof(kCurve) / sizeof(kCurve[0]);
  if (smoothed <= kCurve[0][0]) return 0;
  if (smoothed >= kCurve[n - 1][0]) return 100;
  for (int i = 1; i < n; ++i) {
    if (smoothed <= kCurve[i][0]) {
      float t = (smoothed - kCurve[i - 1][0]) / (kCurve[i][0] - kCurve[i - 1][0]);
      return (int)(kCurve[i - 1][1] + t * (kCurve[i][1] - kCurve[i - 1][1]) + 0.5f);
    }
  }
  return 100;
}

}  // namespace battery
