// Owns the eye controller, both render tasks and the persisted settings.
// All public functions are thread-safe (called from the web server task).
#pragma once
#include <ArduinoJson.h>

namespace engine {

void begin();
void loop();  // call from Arduino loop(): debounced settings save

// Apply a partial state object (API contract in PLAN.md). Returns false + error on bad input.
bool applyState(JsonVariantConst in, String* error);
bool applyAction(JsonVariantConst in, String* error);
void writeState(JsonObject out);
void writeInfo(JsonObject out);
void writeDebug(JsonObject out);  // render timings + optional startup benchmark

// Incremented whenever user-visible state changes (for push notifications).
uint32_t stateVersion();
// Number of loud noises heard so far (and the direction of the latest, -1 left .. +1 right).
uint32_t noiseEventCount(float* direction);

const String& deviceId();   // 12 lowercase hex digits of the MAC
const String& deviceName();
void setIdentity(const String& id);

}  // namespace engine
