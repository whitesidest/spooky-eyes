#include "controller.h"

#include <math.h>
#include <string.h>

namespace eyes {
namespace {

const char* const kMoodNames[kMoodCount] = {"neutral", "angry", "surprised", "sleepy", "asleep"};

constexpr float kBlinkClose = 0.07f;
constexpr float kBlinkOpen = 0.15f;
constexpr float kStartleDuration = 1.6f;
constexpr float kRollDuration = 1.4f;
constexpr float kScanDuration = 4.5f;
constexpr float kDozeDuration = 4.0f;
constexpr float kConvergence = 0.05f;

struct MoodShape {
  float lidTop, lidBot, slant, pupilBias, glow, saccadeScale;
};
const MoodShape kMoodShapes[kMoodCount] = {
    {0.20f, 0.10f, 0.0f, 0.0f, 1.0f, 1.0f},     // neutral
    {0.36f, 0.16f, 0.85f, -0.25f, 1.3f, 1.4f},  // angry
    {0.0f, 0.0f, -0.2f, -0.3f, 1.2f, 1.8f},     // surprised
    {0.55f, 0.22f, -0.1f, 0.2f, 0.55f, 0.3f},   // sleepy
    {1.0f, 1.0f, 0.0f, 0.3f, 0.12f, 0.0f},      // asleep
};

inline float approach(float cur, float target, float rate, float dt) {
  return cur + (target - cur) * (1.0f - expf(-rate * dt));
}
inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline float smooth01(float t) { return t * t * (3.0f - 2.0f * t); }

// Lid closure 0..1 over the course of a blink.
float blinkClosure(float t) {
  if (t < 0) return 0;
  if (t < kBlinkClose) return t / kBlinkClose;
  if (t < kBlinkClose + kBlinkOpen) return 1.0f - (t - kBlinkClose) / kBlinkOpen;
  return 0;
}

// Doze profile: lids sink slowly, hang almost shut, then snap open.
float dozeClosure(float p) {
  if (p < 0.62f) return 0.92f * smooth01(p / 0.62f);
  if (p < 0.82f) return 0.92f + 0.06f * sinf((p - 0.62f) * 31.4f);  // a little twitch
  return 0.92f * (1.0f - smooth01((p - 0.82f) / 0.18f));
}

}  // namespace

const char* moodName(Mood m) { return kMoodNames[(int)m]; }

bool moodFromName(const char* name, Mood* out) {
  for (int i = 0; i < kMoodCount; ++i) {
    if (strcmp(kMoodNames[i], name) == 0) {
      *out = (Mood)i;
      return true;
    }
  }
  return false;
}

EyeController::EyeController(uint32_t seed) : rng_(seed ? seed : 1) {
  pupilPhase_[0] = rand01() * 6.28f;
  pupilPhase_[1] = rand01() * 6.28f;
  eyes_[1].mirror = true;
}

float EyeController::rand01() {
  rng_ ^= rng_ << 13;
  rng_ ^= rng_ >> 17;
  rng_ ^= rng_ << 5;
  return (rng_ & 0xffffff) * (1.0f / 16777216.0f);
}

// Exponential waiting time for an event with the given rate, floored to avoid stutter.
float EyeController::interval(float perMinute) {
  if (perMinute <= 0) return 1e9f;
  float mean = 60.0f / perMinute;
  return 0.3f * mean - logf(1.0f - rand01() * 0.999f) * mean * 0.7f;
}

void EyeController::setAutonomous(bool on) {
  autonomous_ = on;
  if (on) nextSaccade_ = nextSaccade2_ = 0.3f;
}

void EyeController::look(float x, float y, float holdSeconds) {
  tx_ = clampf(x, -1, 1);
  ty_ = clampf(y, -1, 1);
  holding_ = true;
  holdLeft_ = holdSeconds > 0 ? holdSeconds : -1;
  scanT_ = -1;
}

void EyeController::release() {
  holding_ = false;
  nextSaccade_ = 0.2f;
  nextSaccade2_ = 0.25f;
}

void EyeController::blink() {
  blinkT_[0] = blinkT_[1] = 0;
}

void EyeController::wink(int eye) {
  if (eye == 0 || eye == 1) blinkT_[eye] = 0;
}

void EyeController::startle() {
  startleT_ = 0;
  scanT_ = -1;
  dozeT_ = -1;
  tx_ = randRange(-0.6f, 0.6f);
  ty_ = randRange(-0.5f, 0.2f);
}

void EyeController::roll() {
  rollT_ = 0;
  scanT_ = -1;
}

void EyeController::scheduleBlink() {
  float rate = theme_ ? theme_->blinkRate : 0;
  if (rate <= 0) {
    nextBlink_ = 1e9f;
    return;
  }
  // Exponential inter-blink interval, floored so blinks never stutter.
  float mean = 60.0f / rate;
  float u = rand01();
  nextBlink_ = 0.6f - logf(1.0f - u * 0.999f) * mean;
}

void EyeController::pickTarget(float* x, float* y) {
  if (rand01() < 0.3f) {
    *x = randRange(-0.15f, 0.15f);
    *y = randRange(-0.15f, 0.15f);
  } else {
    float a = rand01() * 6.2831853f;
    float r = sqrtf(rand01()) * 0.95f;
    *x = cosf(a) * r;
    *y = sinf(a) * r * 0.75f;  // eyes wander horizontally more than vertically
  }
}

void EyeController::pickSaccade() {
  const float rate = (theme_ ? theme_->saccadeRate : 0.5f) * kMoodShapes[(int)mood_].saccadeScale;
  if (rate <= 0) {
    nextSaccade_ = 1e9f;
    return;
  }
  pickTarget(&tx_, &ty_);
  nextSaccade_ = randRange(0.35f, 1.65f) / rate;
  // People often blink with a big eye movement.
  if (theme_ && theme_->blinkRate > 0 && fabsf(tx_ - gx_) > 0.9f && rand01() < 0.35f) blink();
}

void EyeController::update(float dt) {
  if (!theme_) return;
  time_ += dt;
  const ThemeSpec& t = *theme_;
  const MoodShape& m = kMoodShapes[(int)mood_];
  const bool asleep = mood_ == Mood::Asleep;
  const bool idle = !holding_ && autonomous_ && !asleep && startleT_ < 0 && rollT_ < 0;
  const float moveRate = t.snap ? 0 : (rollT_ >= 0 ? 14.0f : 22.0f);

  // --- Gaze ---
  if (holding_ && holdLeft_ > 0) {
    holdLeft_ -= dt;
    if (holdLeft_ <= 0) release();
  }
  if (idle) {
    nextSaccade_ -= dt;
    if (nextSaccade_ <= 0 && scanT_ < 0) pickSaccade();
    // Searchlight sweep: a slow full-width pass, the signature move of a lidless watcher.
    if (t.scanRate > 0 && scanT_ < 0) {
      nextScan_ -= dt;
      if (nextScan_ <= 0) {
        scanT_ = 0;
        scanDir_ = gx_ < 0 ? 1.0f : -1.0f;
        scanY_ = randRange(-0.25f, 0.2f);
        nextScan_ = interval(t.scanRate * m.saccadeScale);
      }
    }
  }
  float targetX = tx_, targetY = ty_;
  if (scanT_ >= 0) {
    scanT_ += dt;
    float p = scanT_ / kScanDuration;
    if (p >= 1 || !idle) {
      scanT_ = -1;
      nextSaccade_ = randRange(0.4f, 1.2f);
    } else {
      // Ease across, linger at each end.
      float e = smooth01(p);
      tx_ = targetX = scanDir_ * (-0.95f + 1.9f * e);
      ty_ = targetY = scanY_;
    }
  }
  if (rollT_ >= 0) {
    rollT_ += dt;
    float p = rollT_ / kRollDuration;
    if (p >= 1) {
      rollT_ = -1;
    } else {
      float a = 3.14159265f * (1.0f - p);  // left, up, right
      targetX = cosf(a) * 0.95f;
      targetY = -sinf(a) * 0.9f - 0.1f;
    }
  }
  if (t.snap && scanT_ < 0) {
    gx_ = targetX;
    gy_ = targetY;
  } else {
    // Scans always glide, even on mechanical themes.
    float rate = scanT_ >= 0 ? 6.0f : moveRate;
    gx_ = approach(gx_, targetX, rate, dt);
    gy_ = approach(gy_, targetY, rate, dt);
  }
  // Independent right eye: its own saccade schedule while idle, converging when commanded.
  const bool split = t.independence > 0 && idle && scanT_ < 0;
  if (split) {
    nextSaccade2_ -= dt;
    if (nextSaccade2_ <= 0) {
      pickTarget(&tx2_, &ty2_);
      nextSaccade2_ = randRange(0.35f, 1.65f) / ((t.saccadeRate > 0 ? t.saccadeRate : 0.5f) * m.saccadeScale);
    }
  } else {
    tx2_ = targetX;
    ty2_ = targetY;
  }
  if (t.snap && split) {
    gx2_ = tx2_;
    gy2_ = ty2_;
  } else {
    gx2_ = approach(gx2_, tx2_, moveRate > 0 ? moveRate : 22.0f, dt);
    gy2_ = approach(gy2_, ty2_, moveRate > 0 ? moveRate : 22.0f, dt);
  }
  nextJitter_ -= dt;
  if (nextJitter_ <= 0 && t.jitter > 0) {
    float j = t.jitter / (t.gazeRange > 1 ? t.gazeRange : 1);
    jx_ = randRange(-j, j);
    jy_ = randRange(-j, j);
    nextJitter_ = randRange(0.08f, 0.35f);
  }

  // --- Blinks ---
  if (!asleep) {
    nextBlink_ -= dt;
    if (nextBlink_ <= 0) {
      blink();
      scheduleBlink();
      // Occasional double blink.
      if (rand01() < 0.15f) nextBlink_ = 0.35f;
    }
  }
  const float blinkSpeed = t.blinkSpeed > 0 ? t.blinkSpeed : 1.0f;
  float closure[2];
  for (int i = 0; i < 2; ++i) {
    if (blinkT_[i] >= 0) {
      blinkT_[i] += dt * blinkSpeed;
      if (blinkT_[i] > kBlinkClose + kBlinkOpen) blinkT_[i] = -1;
    }
    closure[i] = blinkClosure(blinkT_[i]);
  }

  // --- Doze: lids sag shut, then jolt open (sleepy puppy, bored guard) ---
  float doze = 0;
  if (t.lids && t.dozeRate > 0 && idle && dozeT_ < 0 && mood_ != Mood::Sleepy) {
    nextDoze_ -= dt;
    if (nextDoze_ <= 0) {
      dozeT_ = 0;
      nextDoze_ = interval(t.dozeRate);
    }
  }
  if (dozeT_ >= 0) {
    dozeT_ += dt;
    float p = dozeT_ / kDozeDuration;
    if (p >= 1 || !idle) {
      dozeT_ = -1;
    } else {
      doze = dozeClosure(p);
      if (p > 0.82f && p < 0.9f) {
        // The jolt: a quick glance and a wide-awake pupil.
        tx_ = randRange(-0.3f, 0.3f);
        ty_ = randRange(-0.4f, 0.0f);
      }
    }
  }

  // --- Mood, startle ---
  float lidTopTarget = m.lidTop + t.lidDroop * (1.0f - m.lidTop), lidBotTarget = m.lidBot, slantTarget = m.slant;
  float glowTarget = m.glow, pupilBias = m.pupilBias;
  if (startleT_ >= 0) {
    startleT_ += dt;
    if (startleT_ > kStartleDuration) startleT_ = -1;
    lidTopTarget = lidBotTarget = 0;
    slantTarget = -0.3f;
    glowTarget = 1.5f;
    pupilBias = startleT_ < 0.25f ? -0.45f : 0.4f * (t.pupilDilate > 0 ? t.pupilDilate : 1.0f);
  }
  const float lidRate = startleT_ >= 0 ? 30.0f : 7.0f;
  lidTop_ = approach(lidTop_, lidTopTarget, lidRate, dt);
  lidBot_ = approach(lidBot_, lidBotTarget, lidRate, dt);
  slant_ = approach(slant_, slantTarget, 6.0f, dt);
  glow_ = approach(glow_, glowTarget, 4.0f, dt);

  // --- Pupil ---
  float pupilTarget;
  if (pupilOverride_ >= 0) {
    pupilTarget = pupilOverride_;
  } else {
    pupilTarget = 0.5f + 0.18f * sinf(time_ * 0.37f + pupilPhase_[0]) +
                  0.08f * sinf(time_ * 1.13f + pupilPhase_[1]) + pupilBias;
  }
  pupil_ = approach(pupil_, clampf(pupilTarget, 0, 1), startleT_ >= 0 ? 12.0f : 3.0f, dt);

  // --- Compose per-eye state ---
  const float lazy = t.right.enabled ? clampf(t.right.lazy, 0, 1) : 0;
  for (int i = 0; i < 2; ++i) {
    EyeState& e = eyes_[i];
    float conv = i == 0 ? kConvergence : -kConvergence;
    float bx = gx_, by = gy_;
    if (i == 1) {
      if (t.independence > 0) {
        bx = bx + (gx2_ - bx) * t.independence;
        by = by + (gy2_ - by) * t.independence;
      }
      if (lazy > 0) {
        // Wall-eye: drifts outward and down, only loosely following the other eye.
        bx = bx + (0.45f - bx) * lazy;
        by = by + (0.30f - by) * lazy;
      }
    }
    float x = bx + jx_ + conv, y = by + jy_;
    float len = sqrtf(x * x + y * y);
    if (len > 1) {
      x /= len;
      y /= len;
    }
    e.gazeX = x;
    e.gazeY = y;
    e.pupil = pupil_;
    e.lidSlant = slant_;
    e.time = time_;
    e.mirror = i == 1;
    if (t.lids) {
      float top = fmaxf(lidTop_, closure[i]);
      float bot = fmaxf(lidBot_, closure[i]);
      if (doze > 0) {
        top = fmaxf(top, doze);
        bot = fmaxf(bot, doze * 0.35f);
      }
      e.lidTop = top;
      e.lidBottom = bot;
      e.glow = glow_;
    } else {
      // Lidless eyes flicker instead of blinking.
      e.lidTop = e.lidBottom = 0;
      float flick = closure[i] > 0 ? (0.25f + 0.2f * sinf(time_ * 90.0f)) : 1.0f;
      e.glow = glow_ * fmaxf(flick, 1.0f - closure[i]);
    }
  }
}

}  // namespace eyes
