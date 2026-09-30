// Spooky Eyes firmware entry point.
#include <Arduino.h>

#include "engine.h"
#include "net.h"

void setup() {
  Serial.begin(115200);
  Serial.printf("\nSpooky Eyes %s, PSRAM %u bytes\n", FW_VERSION, (unsigned)ESP.getPsramSize());
  engine::begin();
  net::begin();
}

void loop() {
  engine::loop();
  net::loop();
  delay(5);
}
