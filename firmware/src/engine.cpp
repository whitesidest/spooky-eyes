#include "engine.h"

#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <esp_random.h>
#include <esp_cpu.h>
#include <esp_timer.h>

#include "audio.h"
#include "battery.h"
#include "board.h"
#include "theme_sounds.h"
#include "display.h"
#include "render/controller.h"
#include "render/noise.h"
#include "render/renderer.h"

namespace engine {
namespace {

constexpr uint32_t kSaveDelayMs = 3000;
constexpr uint32_t kMinFrameMs = 25;  // ~40 fps cap

struct Settings {
  bool on = true;
  uint8_t brightness = 200;
  const eyes::ThemeSpec* theme = nullptr;
  eyes::Mood mood = eyes::Mood::Neutral;
  bool autonomous = true;
  float pupil = -1;  // <0 = automatic
  uint8_t volume = 70;
  bool listen = false;       // react to loud noises (startle + glance toward the sound)
  uint8_t sensitivity = 50;  // 0 = only very loud .. 100 = whispers
  bool themeSounds = true;   // play the theme's paired sound when the eyes startle
};

// NVS key for a theme's sound override (keys max 15 chars, so hash the theme id).
String themeSoundKey(const char* themeId) {
  uint32_t h = 2166136261u;
  for (const char* p = themeId; *p; ++p) h = (h ^ (uint8_t)*p) * 16777619u;
  char key[12];
  snprintf(key, sizeof key, "ts%08lx", (unsigned long)h);
  return key;
}

// Mic threshold for reactions: sensitivity 0 -> -18 dBFS, 100 -> -58 dBFS.
float listenThresholdDb(uint8_t sensitivity) { return -18.0f - sensitivity * 0.4f; }

SemaphoreHandle_t lock;
Settings cfg;
eyes::EyeController ctl(esp_random());
eyes::Renderer* ren[2];
eyes::ThemeCache* cache;
eyes::EyeState frame[2];
Preferences prefs;

volatile uint32_t version = 1;
uint32_t savedVersion = 1;
uint32_t lastChangeMs = 0;
volatile float fps = 0;

SemaphoreHandle_t workGo, workDone;
volatile uint32_t noiseEvents = 0;
volatile float noiseDirection = 0;
String id, name;

struct Guard {
  Guard() { xSemaphoreTake(lock, portMAX_DELAY); }
  ~Guard() { xSemaphoreGive(lock); }
};

void changed() {
  version = version + 1;
  lastChangeMs = millis();
}

// Per-eye timing totals (us) since the last log line: setup, shading, waiting on SPI.
volatile uint32_t tBegin[2], tShade[2], tWait[2], tFrames[2];
// Last logged per-frame averages (us) and the startup benchmark, for GET /api/debug.
uint32_t lastBegin[2], lastShade[2], lastWait[2];
String benchLog;

void renderEye(int p) {
  int64_t t0 = esp_timer_get_time();
  ren[p]->begin(frame[p]);
  int64_t t1 = esp_timer_get_time();
  int64_t shade = 0, wait = 0;
  for (int y0 = 0; y0 < display::kHeight; y0 += display::kStripRows) {
    int64_t a = esp_timer_get_time();
    uint16_t* buf = display::beginStrip(p);
    int64_t b = esp_timer_get_time();
    ren[p]->renderRows(y0, y0 + display::kStripRows, buf, true);
    int64_t c = esp_timer_get_time();
    display::pushStrip(p, y0);
    wait += b - a;
    shade += c - b;
  }
  int64_t a = esp_timer_get_time();
  display::finishFrame(p);
  wait += esp_timer_get_time() - a;
  tBegin[p] = tBegin[p] + (uint32_t)(t1 - t0);
  tShade[p] = tShade[p] + (uint32_t)shade;
  tWait[p] = tWait[p] + (uint32_t)wait;
  tFrames[p] = tFrames[p] + 1;
}

void workerTask(void*) {
  for (;;) {
    xSemaphoreTake(workGo, portMAX_DELAY);
    renderEye(1);
    xSemaphoreGive(workDone);
  }
}

#ifdef BENCH_THEMES
// Times one full frame of every theme (shading only, no SPI) and prints it. Build with -DBENCH_THEMES.
void benchThemes() {
  static uint16_t strip[display::kWidth * display::kStripRows];
  eyes::EyeState st;
  st.time = 3.0f;
  st.pupil = 0.5f;
  for (int i = 0; i < eyes::themeCount(); ++i) {
    const eyes::ThemeSpec* t = eyes::themeAt(i);
    int64_t a = esp_timer_get_time();
    cache->build(t);
    int64_t b = esp_timer_get_time();
    ren[0]->begin(st);
    int64_t c = esp_timer_get_time();
    for (int y0 = 0; y0 < display::kHeight; y0 += display::kStripRows)
      ren[0]->renderRows(y0, y0 + display::kStripRows, strip, true);
    int64_t d = esp_timer_get_time();
    char line[96];
    snprintf(line, sizeof line, "%s cache %lld begin %lld shade %lld ms", t->id, (b - a) / 1000, (c - b) / 1000,
             (d - c) / 1000);
    Serial.println(line);
    benchLog += line;
    benchLog += "\n";
  }
  cache->build(cfg.theme);
}
#endif

void renderTask(void*) {
#ifdef BENCH_THEMES
  delay(1500);  // let USB serial attach
  benchThemes();
#endif
  bool panelsOn = true;
  int shownBrightness = -1;
  uint32_t last = micros();
  TickType_t wake = xTaskGetTickCount();
  float fpsAvg = 0;
  for (;;) {
    uint32_t now = micros();
    float dt = (now - last) / 1e6f;
    last = now;
    if (dt > 0.1f) dt = 0.1f;

    bool on;
    int brightness;
    const eyes::ThemeSpec* theme;
    {
      Guard g;
      ctl.update(dt);
      on = cfg.on;
      brightness = cfg.brightness;
      theme = cfg.theme;
      frame[0] = ctl.eye(0);
      frame[1] = ctl.eye(1);
    }
    // Both renderers are idle here, so the shared theme cache can be rebuilt safely.
    if (theme != cache->theme()) {
      uint32_t t0 = millis();
      cache->build(theme);
      Serial.printf("theme %s cached in %lu ms\n", theme->id, (unsigned long)(millis() - t0));
    }

    if (!on) {
      if (panelsOn) {
        for (int p = 0; p < 2; ++p) display::setBacklight(p, 0);
        display::setSleep(true);
        panelsOn = false;
        shownBrightness = -1;
        fps = 0;
      }
      vTaskDelay(pdMS_TO_TICKS(100));
      wake = xTaskGetTickCount();
      continue;
    }
    if (!panelsOn) {
      display::setSleep(false);
      panelsOn = true;
    }

    xSemaphoreGive(workGo);
    renderEye(0);
    xSemaphoreTake(workDone, portMAX_DELAY);

    // Backlight only after a real frame is on the glass (no boot garbage).
    if (brightness != shownBrightness) {
      for (int p = 0; p < 2; ++p) display::setBacklight(p, brightness);
      shownBrightness = brightness;
    }

    float frameS = (micros() - now) / 1e6f;
    float inst = frameS > 0 ? 1.0f / frameS : 0;
    fpsAvg = fpsAvg == 0 ? inst : fpsAvg * 0.95f + inst * 0.05f;
    fps = fpsAvg;
    xTaskDelayUntil(&wake, pdMS_TO_TICKS(kMinFrameMs));
    if (xTaskGetTickCount() - wake > pdMS_TO_TICKS(kMinFrameMs)) wake = xTaskGetTickCount();
  }
}

void loadSettings() {
  prefs.begin("eyes", false);
  cfg.on = prefs.getBool("on", true);
  cfg.brightness = prefs.getUChar("bri", 200);
  String theme = prefs.getString("theme", "sauron");
  cfg.theme = eyes::themeById(theme.c_str());
  if (!cfg.theme) cfg.theme = eyes::themeAt(0);
  eyes::Mood m;
  cfg.mood = eyes::moodFromName(prefs.getString("mood", "neutral").c_str(), &m) ? m : eyes::Mood::Neutral;
  cfg.autonomous = prefs.getBool("auto", true);
  cfg.volume = prefs.getUChar("vol", 70);
  cfg.listen = prefs.getBool("listen", false);
  cfg.sensitivity = prefs.getUChar("sens", 50);
  cfg.themeSounds = prefs.getBool("tsnd", true);
  name = prefs.getString("name", "");
}

void saveSettings() {
  prefs.putBool("on", cfg.on);
  prefs.putUChar("bri", cfg.brightness);
  prefs.putString("theme", cfg.theme->id);
  prefs.putString("mood", eyes::moodName(cfg.mood));
  prefs.putBool("auto", cfg.autonomous);
  prefs.putUChar("vol", cfg.volume);
  prefs.putBool("listen", cfg.listen);
  prefs.putUChar("sens", cfg.sensitivity);
  prefs.putBool("tsnd", cfg.themeSounds);
  prefs.putString("name", name);
}

// The sound paired with a theme: a per-board override ("-" = none) or the built-in default.
// Cached for the current theme so state reads (under the render lock) don't touch flash.
const eyes::ThemeSpec* soundCacheTheme = nullptr;
String soundCache;

String themeSoundFor(const eyes::ThemeSpec* theme) {
  if (theme == soundCacheTheme) return soundCache;
  String override = prefs.getString(themeSoundKey(theme->id).c_str(), "");
  const char* d = theme_sounds::defaultFor(theme->id);
  soundCache = override == "-" ? String() : (override.length() ? override : String(d ? d : ""));
  soundCacheTheme = theme;
  return soundCache;
}

// Startle plus the theme's sound (if enabled). Call with the lock held.
void startleWithSound() {
  ctl.startle();
  if (!cfg.themeSounds) return;
  String sound = themeSoundFor(cfg.theme);
  String ignored;
  if (sound.length()) audio::play(sound.c_str(), &ignored);
}

bool readUnit(JsonVariantConst v, float lo, float hi, float* out) {
  if (!v.is<float>() && !v.is<int>()) return false;
  float f = v.as<float>();
  if (f < lo || f > hi) return false;
  *out = f;
  return true;
}

}  // namespace

void begin() {
  lock = xSemaphoreCreateMutex();
  workGo = xSemaphoreCreateBinary();
  workDone = xSemaphoreCreateBinary();
  loadSettings();
  battery::begin();
  // Crash guard: if the last boot died inside audio setup, skip audio so OTA stays reachable.
  if (prefs.getBool("aud_try", false)) {
    Serial.println("audio: previous init did not finish; skipping audio this boot");
    prefs.putBool("aud_try", false);
  } else {
    prefs.putBool("aud_try", true);
    if (audio::begin()) audio::setVolume(cfg.volume);
    prefs.putBool("aud_try", false);
  }
  ctl.setTheme(cfg.theme);
  ctl.setMood(cfg.mood);
  ctl.setAutonomous(cfg.autonomous);

  cache = new eyes::ThemeCache();
  cache->build(cfg.theme);
  for (int p = 0; p < 2; ++p) {
    ren[p] = new eyes::Renderer();
    ren[p]->setCache(cache);
    ren[p]->setHalfRes(true);
  }

  if (!display::begin()) {
    Serial.println("display init failed");
    return;
  }
  // Render master on core 1 (eye 0); worker on core 0 (eye 1) alongside Wi-Fi.
  xTaskCreatePinnedToCore(workerTask, "eye1", 6144, nullptr, 3, nullptr, 0);
  xTaskCreatePinnedToCore(renderTask, "eye0", 8192, nullptr, 3, nullptr, 1);
}

void loop() {
  battery::update();
  float dir;
  bool listen;
  float threshold;
  {
    Guard g;
    listen = cfg.listen;
    threshold = listenThresholdDb(cfg.sensitivity);
  }
  if (audio::takeLoudEvent(threshold, &dir)) {
    // Every loud noise is reported (WebSocket "noise" event for Home Assistant automations);
    // with listen on, the eyes also jump and stare toward it for a moment.
    noiseDirection = dir;
    noiseEvents = noiseEvents + 1;
    if (listen) {
      Guard g;
      startleWithSound();
      if (dir != 0) ctl.look(dir * 0.9f, 0.05f, 2.5f);
      changed();
    }
  }
  static uint32_t lastLog = 0;
  if (millis() - lastLog > 5000) {
    lastLog = millis();
    Serial.printf("fps %.1f  heap %u  psram %u  theme %s\n", fps, (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getFreePsram(),
                  cfg.theme->id);
    for (int p = 0; p < 2; ++p) {
      uint32_t n = tFrames[p] ? tFrames[p] : 1;
      Serial.printf("  eye%d: begin %lu us  shade %lu us  spi-wait %lu us  (per frame, %lu frames)\n", p,
                    (unsigned long)(tBegin[p] / n), (unsigned long)(tShade[p] / n), (unsigned long)(tWait[p] / n),
                    (unsigned long)tFrames[p]);
      lastBegin[p] = tBegin[p] / n;
      lastShade[p] = tShade[p] / n;
      lastWait[p] = tWait[p] / n;
      tBegin[p] = tShade[p] = tWait[p] = tFrames[p] = 0;
    }
  }
  if (version != savedVersion && millis() - lastChangeMs > kSaveDelayMs) {
    Guard g;
    savedVersion = version;
    saveSettings();
  }
}

bool applyState(JsonVariantConst in, String* error) {
  if (!in.is<JsonObjectConst>()) {
    *error = "expected object";
    return false;
  }
  // Validate everything first so a bad field changes nothing.
  Settings next;
  {
    Guard g;
    next = cfg;
  }
  float f;
  if (!in["on"].isNull()) {
    if (!in["on"].is<bool>()) return *error = "on must be boolean", false;
    next.on = in["on"].as<bool>();
  }
  if (!in["brightness"].isNull()) {
    if (!readUnit(in["brightness"], 0, 255, &f)) return *error = "brightness must be 0-255", false;
    next.brightness = (uint8_t)f;
  }
  if (!in["theme"].isNull()) {
    const eyes::ThemeSpec* t = eyes::themeById(in["theme"] | "");
    if (!t) return *error = "unknown theme", false;
    next.theme = t;
  }
  if (!in["mood"].isNull()) {
    if (!eyes::moodFromName(in["mood"] | "", &next.mood)) return *error = "unknown mood", false;
  }
  if (!in["volume"].isNull()) {
    if (!readUnit(in["volume"], 0, 100, &f)) return *error = "volume must be 0-100", false;
    next.volume = (uint8_t)f;
  }
  if (!in["listen"].isNull()) {
    if (!in["listen"].is<bool>()) return *error = "listen must be boolean", false;
    next.listen = in["listen"].as<bool>();
  }
  if (!in["sensitivity"].isNull()) {
    if (!readUnit(in["sensitivity"], 0, 100, &f)) return *error = "sensitivity must be 0-100", false;
    next.sensitivity = (uint8_t)f;
  }
  if (!in["theme_sounds"].isNull()) {
    if (!in["theme_sounds"].is<bool>()) return *error = "theme_sounds must be boolean", false;
    next.themeSounds = in["theme_sounds"].as<bool>();
  }
  bool hasThemeSound = in.as<JsonObjectConst>()["theme_sound"].is<JsonVariantConst>();
  String themeSound;
  if (hasThemeSound) {
    JsonVariantConst v = in["theme_sound"];
    if (v.isNull()) {
      themeSound = "-";
    } else {
      themeSound = v.as<const char*>() ? v.as<const char*>() : "";
      if (!audio::validName(themeSound.c_str())) return *error = "theme_sound must be a sound name or null", false;
    }
  }
  String newName;
  bool hasName = !in["name"].isNull();
  if (hasName) {
    if (!in["name"].is<const char*>()) return *error = "name must be a string", false;
    newName = in["name"].as<const char*>();
    newName.trim();
    if (newName.length() < 1 || newName.length() > 32) return *error = "name must be 1-32 characters", false;
  }
  if (!in["autonomous"].isNull()) {
    if (!in["autonomous"].is<bool>()) return *error = "autonomous must be boolean", false;
    next.autonomous = in["autonomous"].as<bool>();
  }
  if (in.as<JsonObjectConst>()["pupil"].is<JsonVariantConst>()) {  // key present
    JsonVariantConst p = in["pupil"];
    if (p.isNull()) {
      next.pupil = -1;
    } else {
      if (!readUnit(p, 0, 1, &f)) return *error = "pupil must be null or 0-1", false;
      next.pupil = f;
    }
  }

  Guard g;
  if (hasName) name = newName;
  if (hasThemeSound) {
    prefs.putString(themeSoundKey(next.theme->id).c_str(), themeSound);
    soundCacheTheme = nullptr;
  }
  cfg = next;
  ctl.setTheme(cfg.theme);
  ctl.setMood(cfg.mood);
  if (ctl.autonomous() != cfg.autonomous) ctl.setAutonomous(cfg.autonomous);
  ctl.setPupilOverride(cfg.pupil);
  if (audio::volume() != cfg.volume) audio::setVolume(cfg.volume);
  changed();
  return true;
}

bool applyAction(JsonVariantConst in, String* error) {
  const char* action = in["action"] | "";
  Guard g;
  if (!strcmp(action, "blink")) {
    ctl.blink();
  } else if (!strcmp(action, "wink_left")) {
    ctl.wink(0);
  } else if (!strcmp(action, "wink_right")) {
    ctl.wink(1);
  } else if (!strcmp(action, "startle")) {
    startleWithSound();
  } else if (!strcmp(action, "roll")) {
    ctl.roll();
  } else if (!strcmp(action, "sound")) {
    return audio::play(in["name"] | "", error);
  } else if (!strcmp(action, "tone")) {
    return audio::tone(in["hz"] | 440.0f, in["ms"] | 500, error);
  } else if (!strcmp(action, "stop_sound")) {
    audio::stop();
  } else if (!strcmp(action, "release")) {
    ctl.release();
    changed();
  } else if (!strcmp(action, "look")) {
    float x = in["x"] | 0.0f, y = in["y"] | 0.0f, d = in["duration"] | 0.0f;
    if (x < -1 || x > 1 || y < -1 || y > 1 || d < 0) return *error = "x,y must be -1..1, duration >= 0", false;
    ctl.look(x, y, d);
    changed();
  } else {
    *error = "unknown action";
    return false;
  }
  return true;
}

void writeState(JsonObject out) {
  Guard g;
  out["name"] = deviceName();
  out["on"] = cfg.on;
  out["brightness"] = cfg.brightness;
  out["theme"] = cfg.theme->id;
  out["mood"] = eyes::moodName(cfg.mood);
  out["autonomous"] = cfg.autonomous;
  if (cfg.pupil < 0) out["pupil"] = nullptr;
  else out["pupil"] = cfg.pupil;
  JsonObject gaze = out["gaze"].to<JsonObject>();
  gaze["x"] = roundf(ctl.targetX() * 100) / 100;
  gaze["y"] = roundf(ctl.targetY() * 100) / 100;
  out["volume"] = cfg.volume;
  out["listen"] = cfg.listen;
  out["sensitivity"] = cfg.sensitivity;
  out["theme_sounds"] = cfg.themeSounds;
  String ts = themeSoundFor(cfg.theme);
  if (ts.length()) out["theme_sound"] = ts;
  else out["theme_sound"] = nullptr;
  out["sound_level"] = roundf(audio::level());
  String now = audio::playing();
  if (now.length()) out["playing"] = now;
  else out["playing"] = nullptr;
  if (battery::present()) {
    JsonObject b = out["battery"].to<JsonObject>();
    b["voltage"] = roundf(battery::volts() * 100) / 100;
    b["percent"] = battery::percent();
  } else {
    out["battery"] = nullptr;
  }
  out["rssi"] = WiFi.isConnected() ? WiFi.RSSI() : 0;
  out["fps"] = roundf(fps * 10) / 10;
  out["uptime"] = millis() / 1000;
}

void writeInfo(JsonObject out) {
  out["id"] = id;
  out["name"] = deviceName();
  out["model"] = BOARD_MODEL;
  out["fw"] = FW_VERSION;
  char mac[18];
  snprintf(mac, sizeof mac, "%c%c:%c%c:%c%c:%c%c:%c%c:%c%c", id[0], id[1], id[2], id[3], id[4], id[5], id[6], id[7],
           id[8], id[9], id[10], id[11]);
  out["mac"] = id.length() == 12 ? String(mac) : WiFi.macAddress();
  out["ip"] = WiFi.localIP().toString();
  out["eyes"] = 2;
  JsonObject features = out["features"].to<JsonObject>();
  features["speaker"] = audio::available();
  features["microphone"] = audio::hasMicrophones();
  features["battery"] = true;
  JsonArray themes = out["themes"].to<JsonArray>();
  for (int i = 0; i < eyes::themeCount(); ++i) {
    JsonObject t = themes.add<JsonObject>();
    t["id"] = eyes::themeAt(i)->id;
    t["name"] = eyes::themeAt(i)->name;
    t["category"] = eyes::themeAt(i)->category ? eyes::themeAt(i)->category : "other";
    const char* snd = theme_sounds::defaultFor(eyes::themeAt(i)->id);
    if (snd) t["sound"] = snd;
    else t["sound"] = nullptr;
  }
  JsonArray moods = out["moods"].to<JsonArray>();
  for (int i = 0; i < eyes::kMoodCount; ++i) moods.add(eyes::moodName((eyes::Mood)i));
}

void writeDebug(JsonObject out) {
  out["fps"] = fps;
  out["theme"] = cfg.theme->id;
  out["heap"] = ESP.getFreeHeap();
  out["psram"] = ESP.getFreePsram();
  JsonArray eyes = out["eyes"].to<JsonArray>();
  for (int p = 0; p < 2; ++p) {
    JsonObject e = eyes.add<JsonObject>();
    e["begin_us"] = lastBegin[p];
    e["shade_us"] = lastShade[p];
    e["spi_wait_us"] = lastWait[p];
  }
  out["bench"] = benchLog;
  out["cpu_mhz"] = getCpuFrequencyMhz();
  // Calibration: cycles per dependent float multiply-add and per vnoise() call.
  volatile float acc = 1.0f;
  float x = acc;
  uint32_t c0 = esp_cpu_get_cycle_count();
  for (int i = 0; i < 100000; ++i) x = x * 0.99999f + 0.00001f;
  uint32_t c1 = esp_cpu_get_cycle_count();
  acc = x;
  out["cycles_per_fma"] = (c1 - c0) / 100000.0f;
  float n = 0;
  c0 = esp_cpu_get_cycle_count();
  for (int i = 0; i < 2000; ++i) n += eyes::vnoise(i * 0.37f, i * 0.11f, 1.3f);
  c1 = esp_cpu_get_cycle_count();
  acc = n;
  out["cycles_per_vnoise"] = (c1 - c0) / 2000.0f;
}

uint32_t stateVersion() { return version; }

uint32_t noiseEventCount(float* direction) {
  *direction = noiseDirection;
  return noiseEvents;
}

const String& deviceId() { return id; }

const String& deviceName() {
  static String fallback;
  if (name.length()) return name;
  fallback = "Spooky Eyes " + id.substring(6);
  return fallback;
}

void setIdentity(const String& macHex) { id = macHex; }

}  // namespace engine
