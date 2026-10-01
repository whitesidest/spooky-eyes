// Speaker (ES8311) + microphones (ES7210): built-in effects, uploaded WAV clips, loudness sensing.
// All functions are thread-safe; the audio runs on its own task.
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

namespace audio {

bool begin();  // false if the codecs don't answer (board without audio)
bool available();
bool hasMicrophones();

void setVolume(int volume);  // 0-100
int volume();

// Play a built-in effect or an uploaded clip by name. Replaces whatever is playing.
bool play(const char* name, String* error);
bool tone(float hz, int ms, String* error);
void stop();
String playing();  // name of the current sound, or empty

// Microphone loudness (dBFS, ~ -90 quiet .. 0 clipping), smoothed peak over the last ~0.5 s.
float level();
// Loud-noise detection for reactions: returns true once per event (with ~2 s cooldown).
// direction is -1 (left) .. +1 (right) from the two mic channels, 0 if unknown.
bool takeLoudEvent(float thresholdDb, float* direction);

// Sound library (uploaded clips live in LittleFS under /sounds/<name>.wav).
void listSounds(JsonObject out);
bool validName(const char* name);
String clipPath(const char* name);
bool removeClip(const char* name);
size_t freeBytes();

}  // namespace audio
