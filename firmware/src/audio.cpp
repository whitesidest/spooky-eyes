#include "audio.h"

#include <ESP_I2S.h>
#include <LittleFS.h>
#include <Wire.h>
#include <math.h>

#include "board.h"
#include "es7210.h"
#include "es8311.h"

namespace audio {
namespace {

constexpr uint32_t kMicRate = 16000;
constexpr int kMclkMultiple = 256;
constexpr uint32_t kCooldownMs = 2000;
constexpr uint32_t kAfterPlaybackMs = 500;  // ignore our own speaker echo
constexpr float kEffectGain = 0.7f;

struct Effect {
  const char* name;
  float seconds;
};
const Effect kEffects[] = {{"growl", 2.2f},   {"heartbeat", 2.6f}, {"whisper", 2.6f}, {"creak", 2.0f},
                           {"zap", 0.9f},     {"chime", 2.2f},     {"test", 1.0f}};
constexpr int kEffectCount = sizeof(kEffects) / sizeof(kEffects[0]);

I2SClass i2s;
es8311_handle_t speaker = nullptr;
es7210_dev_handle_t mics = nullptr;
bool ok = false;
bool fsOk = false;
volatile int vol = 70;
uint32_t rate = 0;

enum class Kind : uint8_t { None, Effect, Clip, Tone };
struct Request {
  Kind kind = Kind::None;
  char name[32] = {};
  float hz = 0;
  int ms = 0;
};
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
Request pending;
volatile bool hasPending = false;
volatile bool stopRequested = false;
char nowPlaying[32] = {};
volatile bool isPlaying = false;
volatile uint32_t playbackEndMs = 0;

volatile float levelDb = -90;
volatile float maxDbSinceCheck = -90;
volatile float direction = 0;
uint32_t lastLoudMs = 0;

bool probe(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

void setRate(uint32_t r) {
  if (r == rate) return;
  if (speaker) es8311_sample_frequency_config(speaker, r * kMclkMultiple, r);
  i2s.configureTX(r, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO);
  i2s.configureRX(r, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO);
  rate = r;
}

void writeAll(const int16_t* data, size_t bytes) {
  const uint8_t* p = (const uint8_t*)data;
  while (bytes) {
    size_t n = i2s.write(p, bytes);
    if (!n) vTaskDelay(1);
    p += n;
    bytes -= n;
  }
}

bool interrupted() { return stopRequested || hasPending; }

// --- Built-in effects: generated at 16 kHz, so nothing needs to be stored. -------------------

struct Synth {
  uint32_t rng = 0x9E3779B9u;
  float phase = 0, lp = 0, lp2 = 0, jitter = 0;
  float noise() {
    rng ^= rng << 13;
    rng ^= rng >> 17;
    rng ^= rng << 5;
    return (rng & 0xffff) * (2.0f / 65535.0f) - 1.0f;
  }
};

inline float envelope(float t, float dur, float attack, float release) {
  float a = attack > 0 ? fminf(t / attack, 1.0f) : 1.0f;
  float r = release > 0 ? fminf((dur - t) / release, 1.0f) : 1.0f;
  return fmaxf(0.0f, fminf(a, r));
}

float effectSample(int id, float t, float dur, float dt, Synth& s) {
  constexpr float kTwoPi = 6.2831853f;
  switch (id) {
    case 0: {  // growl: rough sawtooth with wobbling pitch, rasp and breath noise
      float f = 78.0f + 14.0f * sinf(kTwoPi * 2.3f * t) + 7.0f * sinf(kTwoPi * 6.7f * t);
      s.phase += f * dt;
      s.phase -= floorf(s.phase);
      float saw = 2.0f * s.phase - 1.0f;
      float x = saw * (0.75f + 0.25f * sinf(kTwoPi * 23.0f * t)) + 0.35f * s.noise();
      s.lp += (x - s.lp) * 0.22f;
      return 1.6f * s.lp * envelope(t, dur, 0.25f, 0.6f);
    }
    case 1: {  // heartbeat: lub-dub thumps
      float tb = fmodf(t, 0.85f);
      float v = 0;
      if (tb < 0.18f) v += sinf(kTwoPi * (120.0f - 120.0f * tb) * tb) * expf(-tb * 26.0f);
      float td = tb - 0.27f;
      if (td > 0 && td < 0.18f) v += 0.75f * sinf(kTwoPi * (105.0f - 100.0f * td) * td) * expf(-td * 30.0f);
      s.lp += (s.noise() - s.lp) * 0.05f;
      return 1.2f * (v + 0.15f * s.lp * (tb < 0.05f ? 1.0f : 0.0f));
    }
    case 2: {  // whisper: band-limited breath with syllable-like bursts
      float n = s.noise();
      s.lp += (n - s.lp) * 0.45f;
      s.lp2 += (s.lp - s.lp2) * 0.06f;
      float hp = s.lp - s.lp2;
      float syll = 0.5f + 0.5f * sinf(kTwoPi * 4.1f * t + 2.0f * sinf(kTwoPi * 0.9f * t));
      return 1.8f * hp * syll * syll * envelope(t, dur, 0.3f, 0.7f);
    }
    case 3: {  // creak: stick-slip pulses through a resonance (old door)
      s.jitter += (s.noise() - s.jitter) * 0.002f;
      float rate = 38.0f + 20.0f * sinf(kTwoPi * 0.45f * t) + 40.0f * s.jitter;
      s.phase += rate * dt;
      float pulse = 0;
      if (s.phase >= 1.0f) {
        s.phase -= 1.0f;
        pulse = 1.0f;
      }
      s.lp = s.lp * 0.985f + pulse;  // decaying excitation
      float ring = sinf(kTwoPi * 880.0f * t) * s.lp + 0.5f * sinf(kTwoPi * 1370.0f * t) * s.lp;
      return 0.35f * ring * envelope(t, dur, 0.1f, 0.4f);
    }
    case 4: {  // zap: falling sine sweep, twice
      float tz = t < 0.45f ? t : t - 0.45f;
      float f = 1700.0f * expf(-tz * 7.0f) + 160.0f;
      s.phase += f * dt;
      s.phase -= floorf(s.phase);
      return sinf(kTwoPi * s.phase) * expf(-tz * 3.0f);
    }
    case 5: {  // chime: two bell strikes with inharmonic partials
      float v = 0;
      const float base[2] = {659.25f, 523.25f};
      for (int k = 0; k < 2; ++k) {
        float tk = t - k * 0.7f;
        if (tk < 0) continue;
        float d = expf(-tk * 2.2f);
        v += d * (sinf(kTwoPi * base[k] * tk) + 0.5f * sinf(kTwoPi * base[k] * 2.76f * tk) * expf(-tk * 3.0f) +
                  0.3f * sinf(kTwoPi * base[k] * 5.4f * tk) * expf(-tk * 6.0f));
      }
      return 0.45f * v;
    }
    default:  // test tone
      return 0.6f * sinf(kTwoPi * 440.0f * t) * envelope(t, dur, 0.02f, 0.05f);
  }
}

void playEffect(int id) {
  setRate(kMicRate);
  Synth s;
  const float dur = kEffects[id].seconds, dt = 1.0f / kMicRate;
  int16_t buf[256 * 2];
  const uint32_t total = (uint32_t)(dur * kMicRate);
  for (uint32_t i = 0; i < total && !interrupted();) {
    int frames = 0;
    for (; frames < 256 && i < total; ++frames, ++i) {
      float v = effectSample(id, i * dt, dur, dt, s) * kEffectGain;
      v = v > 1 ? 1 : (v < -1 ? -1 : v);
      int16_t sample = (int16_t)(v * 32767.0f);
      buf[frames * 2] = buf[frames * 2 + 1] = sample;
    }
    writeAll(buf, frames * 4);
  }
}

void playTone(float hz, int ms) {
  setRate(kMicRate);
  int16_t buf[256 * 2];
  const uint32_t total = (uint32_t)ms * kMicRate / 1000;
  float phase = 0;
  for (uint32_t i = 0; i < total && !interrupted();) {
    int frames = 0;
    for (; frames < 256 && i < total; ++frames, ++i) {
      float t = (float)i / kMicRate, dur = ms / 1000.0f;
      phase += hz / kMicRate;
      phase -= floorf(phase);
      int16_t sample = (int16_t)(0.5f * 32767.0f * sinf(6.2831853f * phase) * envelope(t, dur, 0.01f, 0.03f));
      buf[frames * 2] = buf[frames * 2 + 1] = sample;
    }
    writeAll(buf, frames * 4);
  }
}

// 16-bit PCM WAV, mono or stereo, 8-48 kHz.
bool playClip(const char* name) {
  File f = LittleFS.open(clipPath(name), "r");
  if (!f) return false;
  char id[4];
  uint32_t size;
  if (f.read((uint8_t*)id, 4) != 4 || memcmp(id, "RIFF", 4) || f.read((uint8_t*)&size, 4) != 4 ||
      f.read((uint8_t*)id, 4) != 4 || memcmp(id, "WAVE", 4))
    return false;
  uint16_t format = 0, channels = 0, bits = 0;
  uint32_t sampleRate = 0;
  while (f.available()) {
    if (f.read((uint8_t*)id, 4) != 4 || f.read((uint8_t*)&size, 4) != 4) return false;
    if (!memcmp(id, "fmt ", 4)) {
      uint8_t fmt[16];
      if (size < 16 || f.read(fmt, 16) != 16) return false;
      memcpy(&format, fmt, 2);
      memcpy(&channels, fmt + 2, 2);
      memcpy(&sampleRate, fmt + 4, 4);
      memcpy(&bits, fmt + 14, 2);
      f.seek(f.position() + size - 16 + (size & 1));
    } else if (!memcmp(id, "data", 4)) {
      if (format != 1 || bits != 16 || channels < 1 || channels > 2 || sampleRate < 8000 || sampleRate > 48000)
        return false;
      setRate(sampleRate);
      int16_t in[512], out[512];
      // Mono reads half as many bytes so each block still fits the stereo output buffer.
      const size_t chunk = channels == 2 ? sizeof(in) : sizeof(out) / 2;
      uint32_t left = size;
      while (left && !interrupted()) {
        size_t got = f.read((uint8_t*)in, left < chunk ? left : chunk);
        if (!got) break;
        left -= got;
        if (channels == 2) {
          writeAll(in, got & ~3u);
        } else {
          size_t n = got / 2;
          for (size_t k = 0; k < n; ++k) out[2 * k] = out[2 * k + 1] = in[k];
          writeAll(out, n * 4);
        }
      }
      return true;
    } else {
      f.seek(f.position() + size + (size & 1));
    }
  }
  return false;
}

void listenBlock() {
  int16_t buf[256 * 2];
  size_t n = i2s.readBytes((char*)buf, sizeof buf);
  int frames = n / 4;
  if (frames <= 0) return;
  int64_t l2 = 0, r2 = 0;
  for (int i = 0; i < frames; ++i) {
    int32_t l = buf[2 * i], r = buf[2 * i + 1];
    l2 += l * l;
    r2 += r * r;
  }
  float lr = sqrtf((float)l2 / frames), rr = sqrtf((float)r2 / frames);
  float rms = sqrtf(((float)l2 + (float)r2) / (2.0f * frames));
  float db = 20.0f * log10f(rms / 32768.0f + 1e-6f);
  // Fast attack, slow release, like a VU meter.
  levelDb = db > levelDb ? db : levelDb * 0.9f + db * 0.1f;
  if (db > maxDbSinceCheck) {
    maxDbSinceCheck = db;
    // Only trust left/right when both channels carry a microphone (on the DualEye the second
    // I2S slot is ~25 dB down, i.e. effectively one mic), otherwise report "unknown" (0).
    const float lo = lr < rr ? lr : rr, hi = lr < rr ? rr : lr;
    direction = (hi > 1 && lo > hi * 0.2f) ? (rr - lr) / (rr + lr) : 0;
  }
}

void audioTask(void*) {
  for (;;) {
    if (hasPending) {
      Request req;
      portENTER_CRITICAL(&mux);
      req = pending;
      hasPending = false;
      stopRequested = false;
      portEXIT_CRITICAL(&mux);

      strlcpy(nowPlaying, req.name, sizeof nowPlaying);
      isPlaying = true;
      digitalWrite(PIN_SPEAKER_AMP, HIGH);
      if (req.kind == Kind::Effect) {
        for (int i = 0; i < kEffectCount; ++i)
          if (!strcmp(kEffects[i].name, req.name)) playEffect(i);
      } else if (req.kind == Kind::Clip) {
        playClip(req.name);
      } else if (req.kind == Kind::Tone) {
        playTone(req.hz, req.ms);
      }
      // Let the DMA drain with silence so the amp doesn't pop, then go back to listening.
      int16_t silence[256 * 2] = {};
      writeAll(silence, sizeof silence);
      if (!hasPending) digitalWrite(PIN_SPEAKER_AMP, LOW);
      isPlaying = false;
      nowPlaying[0] = 0;
      playbackEndMs = millis();
      setRate(kMicRate);
      continue;
    }
    listenBlock();
  }
}

void request(Kind kind, const char* name, float hz, int ms) {
  portENTER_CRITICAL(&mux);
  pending.kind = kind;
  strlcpy(pending.name, name, sizeof pending.name);
  pending.hz = hz;
  pending.ms = ms;
  hasPending = true;
  portEXIT_CRITICAL(&mux);
}

}  // namespace

bool begin() {
  pinMode(PIN_SPEAKER_AMP, OUTPUT);
  digitalWrite(PIN_SPEAKER_AMP, LOW);
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
  fsOk = LittleFS.begin(true, "/littlefs", 10, "spiffs");
  if (fsOk) LittleFS.mkdir("/sounds");

  i2s.setPins(PIN_I2S_BCLK, PIN_I2S_LRCK, PIN_I2S_DOUT, PIN_I2S_DIN, PIN_I2S_MCLK);
  i2s.setTimeout(100);
  if (!i2s.begin(I2S_MODE_STD, kMicRate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO)) {
    Serial.println("audio: I2S init failed");
    return false;
  }
  rate = kMicRate;

  if (!probe(ES8311_I2C_ADDR)) {
    Serial.println("audio: ES8311 not found");
    return false;
  }
  speaker = es8311_create(I2C_NUM_0, ES8311_I2C_ADDR);
  const es8311_clock_config_t clk = {
      .mclk_inverted = false,
      .sclk_inverted = false,
      .mclk_from_mclk_pin = true,
      .mclk_frequency = (int)(kMicRate * kMclkMultiple),
      .sample_frequency = (int)kMicRate,
  };
  if (!speaker || es8311_init(speaker, &clk, ES8311_RESOLUTION_16, ES8311_RESOLUTION_16) != ESP_OK) {
    Serial.println("audio: ES8311 init failed");
    return false;
  }
  es8311_voice_volume_set(speaker, vol, nullptr);
  es8311_microphone_config(speaker, false);

  if (probe(ES7210_I2C_ADDR)) {
    es7210_i2c_config_t conf = {.i2c_port = I2C_NUM_0, .i2c_addr = ES7210_I2C_ADDR};
    if (es7210_new_codec(&conf, &mics) == ESP_OK) {
      es7210_codec_config_t cc = {};
      cc.sample_rate_hz = kMicRate;
      cc.mclk_ratio = kMclkMultiple;
      cc.i2s_format = ES7210_I2S_FMT_I2S;
      cc.bit_width = ES7210_I2S_BITS_16B;
      cc.mic_bias = ES7210_MIC_BIAS_2V87;
      cc.mic_gain = ES7210_MIC_GAIN_30DB;
      cc.flags.tdm_enable = true;
      if (es7210_config_codec(mics, &cc) != ESP_OK || es7210_config_volume(mics, 10) != ESP_OK) mics = nullptr;
    }
  }
  if (!mics) Serial.println("audio: ES7210 microphones not available");

  ok = true;
  xTaskCreatePinnedToCore(audioTask, "audio", 6144, nullptr, 4, nullptr, 0);
  Serial.printf("audio: ready (mics %s, storage %s)\n", mics ? "yes" : "no", fsOk ? "yes" : "no");
  return true;
}

bool available() { return ok; }

bool hasMicrophones() { return ok && mics; }

void setVolume(int v) {
  vol = v < 0 ? 0 : (v > 100 ? 100 : v);
  if (speaker) es8311_voice_volume_set(speaker, vol, nullptr);
}

int volume() { return vol; }

bool play(const char* name, String* error) {
  if (!ok) return *error = "audio not available", false;
  for (int i = 0; i < kEffectCount; ++i) {
    if (!strcmp(kEffects[i].name, name)) {
      request(Kind::Effect, name, 0, 0);
      return true;
    }
  }
  if (!validName(name) || !fsOk || !LittleFS.exists(clipPath(name))) return *error = "unknown sound", false;
  request(Kind::Clip, name, 0, 0);
  return true;
}

bool tone(float hz, int ms, String* error) {
  if (!ok) return *error = "audio not available", false;
  if (hz < 20 || hz > 8000 || ms < 10 || ms > 10000) return *error = "tone needs hz 20-8000, ms 10-10000", false;
  request(Kind::Tone, "tone", hz, ms);
  return true;
}

void stop() { stopRequested = true; }

String playing() { return isPlaying ? String(nowPlaying) : String(); }

float level() { return mics ? levelDb : -90.0f; }

bool takeLoudEvent(float thresholdDb, float* dir) {
  if (!mics) return false;
  float peak = maxDbSinceCheck;
  maxDbSinceCheck = -90;
  uint32_t now = millis();
  if (isPlaying || now - playbackEndMs < kAfterPlaybackMs) return false;
  if (peak < thresholdDb || now - lastLoudMs < kCooldownMs) return false;
  lastLoudMs = now;
  *dir = direction;
  return true;
}

bool validName(const char* name) {
  size_t n = strlen(name);
  if (n < 1 || n > 24) return false;
  for (size_t i = 0; i < n; ++i) {
    char c = name[i];
    if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-')) return false;
  }
  return true;
}

String clipPath(const char* name) { return String("/sounds/") + name + ".wav"; }

bool removeClip(const char* name) { return fsOk && validName(name) && LittleFS.remove(clipPath(name)); }

size_t freeBytes() { return fsOk ? LittleFS.totalBytes() - LittleFS.usedBytes() : 0; }

void listSounds(JsonObject out) {
  JsonArray builtin = out["builtin"].to<JsonArray>();
  for (const auto& e : kEffects) builtin.add(e.name);
  JsonArray clips = out["clips"].to<JsonArray>();
  if (fsOk) {
    File dir = LittleFS.open("/sounds");
    for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
      String n = f.name();
      if (!n.endsWith(".wav")) continue;
      JsonObject c = clips.add<JsonObject>();
      c["name"] = n.substring(0, n.length() - 4);
      c["bytes"] = f.size();
    }
  }
  out["free_bytes"] = freeBytes();
}

}  // namespace audio
