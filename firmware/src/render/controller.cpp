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

// Lid closure 0..1 over the course of a blink.
float blinkClosure(float t) {
  if (t < 0) return 0;
  if (t < kBlinkClose) return t / kBlinkClose;
  if (t < kBlinkClose + kBlinkOpen) return 1.0f - (t - kBlinkClose) / kBlinkOpen;
  return 0;
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

void EyeController::setAutonomous(bool on) {
  autonomous_ = on;
  if (on) nextSaccade_ = 0.3f;
}

void EyeController::look(float x, float y, float holdSeconds) {
  tx_ = clampf(x, -1, 1);
  ty_ = clampf(y, -1, 1);
  holding_ = true;
  holdLeft_ = holdSeconds > 0 ? holdSeconds : -1;
}

void EyeController::release() {
  holding_ = false;
  nextSaccade_ = 0.2f;
}

void EyeController::blink() {
  blinkT_[0] = blinkT_[1] = 0;
}

void EyeController::wink(int eye) {
  if (eye == 0 || eye == 1) blinkT_[eye] = 0;
}

void EyeController::startle() {
  startleT_ = 0;
  tx_ = randRange(-0.6f, 0.6f);
  ty_ = randRange(-0.5f, 0.2f);
}

void EyeController::roll() { rollT_ = 0; }

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

void EyeController::pickSaccade() {
  const float rate = (theme_ ? theme_->saccadeRate : 0.5f) * kMoodShapes[(int)mood_].saccadeScale;
  if (rate <= 0) {
    nextSaccade_ = 1e9f;
    return;
  }
  if (rand01() < 0.3f) {
    tx_ = randRange(-0.15f, 0.15f);
    ty_ = randRange(-0.15f, 0.15f);
  } else {
    float a = rand01() * 6.2831853f;
    float r = sqrtf(rand01()) * 0.95f;
    tx_ = cosf(a) * r;
    ty_ = sinf(a) * r * 0.75f;  // eyes wander horizontally more than vertically
  }
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

  // --- Gaze ---
  if (holding_ && holdLeft_ > 0) {
    holdLeft_ -= dt;
    if (holdLeft_ <= 0) release();
  }
  if (!holding_ && autonomous_ && !asleep) {
    nextSaccade_ -= dt;
    if (nextSaccade_ <= 0) pickSaccade();
  }
  float targetX = tx_, targetY = ty_;
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
  if (t.snap) {
    gx_ = targetX;
    gy_ = targetY;
  } else {
    gx_ = approach(gx_, targetX, rollT_ >= 0 ? 14.0f : 22.0f, dt);
    gy_ = approach(gy_, targetY, rollT_ >= 0 ? 14.0f : 22.0f, dt);
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
  float closure[2];
  for (int i = 0; i < 2; ++i) {
    if (blinkT_[i] >= 0) {
      blinkT_[i] += dt;
      if (blinkT_[i] > kBlinkClose + kBlinkOpen) blinkT_[i] = -1;
    }
    closure[i] = blinkClosure(blinkT_[i]);
  }

  // --- Mood, startle ---
  float lidTopTarget = m.lidTop, lidBotTarget = m.lidBot, slantTarget = m.slant;
  float glowTarget = m.glow, pupilBias = m.pupilBias;
  if (startleT_ >= 0) {
    startleT_ += dt;
    if (startleT_ > kStartleDuration) startleT_ = -1;
    lidTopTarget = lidBotTarget = 0;
    slantTarget = -0.3f;
    glowTarget = 1.5f;
    pupilBias = startleT_ < 0.25f ? -0.45f : 0.4f;  // snap small, then flood wide
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
  for (int i = 0; i < 2; ++i) {
    EyeState& e = eyes_[i];
    float conv = i == 0 ? kConvergence : -kConvergence;
    float x = gx_ + jx_ + conv, y = gy_ + jy_;
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
      e.lidTop = fmaxf(lidTop_, closure[i]);
      e.lidBottom = fmaxf(lidBot_, closure[i]);
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
