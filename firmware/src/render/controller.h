// Behaviour engine: turns themes, moods and commands into per-eye EyeState.
#pragma once
#include <stdint.h>

#include "renderer.h"
#include "theme.h"

namespace eyes {

enum class Mood : uint8_t { Neutral, Angry, Surprised, Sleepy, Asleep };
constexpr int kMoodCount = 5;
const char* moodName(Mood m);
bool moodFromName(const char* name, Mood* out);

class EyeController {
 public:
  explicit EyeController(uint32_t seed = 0x5eed1234u);

  void setTheme(const ThemeSpec* theme) { theme_ = theme; }
  void setMood(Mood m) { mood_ = m; }
  Mood mood() const { return mood_; }
  void setAutonomous(bool on);
  bool autonomous() const { return autonomous_; }
  void setPupilOverride(float v) { pupilOverride_ = v; }  // <0 = automatic
  float pupilOverride() const { return pupilOverride_; }

  // x,y in -1..1. holdSeconds <= 0 holds until release().
  void look(float x, float y, float holdSeconds);
  void release();
  void blink();
  void wink(int eye);  // 0 = left, 1 = right
  void startle();
  void roll();

  void update(float dt);
  const EyeState& eye(int i) const { return eyes_[i]; }
  float targetX() const { return tx_; }
  float targetY() const { return ty_; }

 private:
  float rand01();
  float randRange(float a, float b) { return a + (b - a) * rand01(); }
  void scheduleBlink();
  void pickSaccade();

  const ThemeSpec* theme_ = nullptr;
  uint32_t rng_;
  Mood mood_ = Mood::Neutral;
  bool autonomous_ = true;
  float pupilOverride_ = -1;

  float time_ = 0;
  float gx_ = 0, gy_ = 0;  // current gaze
  float tx_ = 0, ty_ = 0;  // target gaze
  float jx_ = 0, jy_ = 0, nextJitter_ = 0;
  float nextSaccade_ = 1;
  bool holding_ = false;
  float holdLeft_ = 0;  // <0 = indefinitely

  float nextBlink_ = 2;
  float blinkT_[2] = {-1, -1};  // elapsed time of the running blink per eye, -1 = none
  float startleT_ = -1;
  float rollT_ = -1;

  float lidTop_ = 0.1f, lidBot_ = 0.05f, slant_ = 0, pupil_ = 0.5f, glow_ = 1;
  float pupilPhase_[2] = {0, 0};

  EyeState eyes_[2];
};

}  // namespace eyes
