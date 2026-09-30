// Theme definitions: every look is data, shaded procedurally by Renderer.
#pragma once
#include <stdint.h>

namespace eyes {

struct Rgb {
  float r, g, b;
};

// Bar = goat/demon horizontal; Triangle = carved jack-o'-lantern eye (use with pupilInvert);
// Star = five-point star; Clover = shamrock; Cross = an "X" (cartoon dead eye).
enum class PupilShape : uint8_t { Round, Slit, None, Bar, Heart, Triangle, Star, Clover, Cross };
// Pit = a socket full of flames: hot core low down, tall tongues rising, dark rim.
enum class FireMode : uint8_t { None, Rising, Radial, Pit };

// Optional overrides for the right-hand panel so the two eyes can differ (heterochromia,
// one dead eye, ...). Zero fields mean "same as the left eye".
struct EyeVariant {
  bool enabled;
  Rgb irisInner, irisOuter;  // used when enabled
  Rgb pupilColor;
  Rgb glowColor;
  float pupilScale;  // multiplies the pupil size (0 = unchanged)
  float glowScale;   // multiplies glow amount (0 = unchanged)
  float haze;        // 0..1 milky cataract veil over the iris (dead eye)
  float lazy;        // 0..1 this eye drifts outward/down instead of tracking (wall-eye)
};

struct ThemeSpec {
  const char* id;
  const char* name;

  // Sclera (the "white"). edge is blended in toward the display rim.
  Rgb sclera;
  Rgb scleraEdge;
  float veins;      // 0..1 red vein amount
  float rings;      // 0..1 concentric machined rings (metal housings)

  // Iris
  float irisRadius;  // px
  Rgb irisInner;
  Rgb irisOuter;
  Rgb limbus;        // dark ring at the iris edge
  float limbusWidth; // px
  float striation;   // 0..1 radial fibre contrast
  float striationFreq;
  float irisSwirl;   // rad/s the fibre pattern drifts

  // Pupil
  PupilShape pupilShape;
  Rgb pupilColor;
  float pupilMin, pupilMax;  // radius (round) or half-width (slit), px
  float slitHeight;          // slit half-height as a fraction of irisRadius

  // Fire field
  FireMode fire;
  Rgb fireLow, fireMid, fireHigh;
  float fireScale;   // noise frequency
  float fireSpeed;
  float fireAmount;  // 0..1
  float fireInner, fireOuter;  // radial band (px from iris centre) the fire lives in

  // Glow (additive bloom around the iris centre)
  Rgb glowColor;
  float glowRadius;
  float glowAmount;
  float glowPulse;   // Hz, 0 = steady

  float specular;    // 0..1 highlight strength
  float scanlines;   // 0..1

  // Eyelids
  bool lids;
  Rgb lidColor;
  Rgb lidEdge;

  // Motion personality
  float gazeRange;    // px the iris may travel from centre
  float saccadeRate;  // new gaze targets per second
  float blinkRate;    // blinks per minute (0 = never; lidless themes flicker instead)
  float jitter;       // micro-saccade amplitude, px
  bool snap;          // mechanical instant moves instead of eased

  // Optional extras (zero = off); keep new fields at the end so older themes can omit them.
  float spiral;       // 0..1 hypnotic spiral bands across the iris (irisInner/irisOuter)
  float spiralSpeed;  // rad/s
  float hueSpin;      // iris hue rotations per second
  bool pupilInvert;   // pupilColor fills everything OUTSIDE the pupil shape (cut-out look)
  const char* category;  // grouping for UIs: classic, halloween, creatures, sci-fi, holidays, fun

  // --- v1.2 extras ---
  float sparkle;      // 0..1 twinkling star highlights on the iris (anime, wet puppy eyes)
  float lidDroop;     // 0..1 baseline heaviness added to the top lid in every mood
  float blinkSpeed;   // blink duration multiplier (0 = 1; 0.5 = slow lazy blinks)
  float independence; // 0..1 how much the right eye wanders on its own (chameleon)
  float scanRate;     // slow full-width searchlight sweeps per minute
  float dozeRate;     // per minute: lids drift shut, then snap open again
  uint8_t cluster;    // >0: number of sub-eyes per panel from the built-in cluster layout
  float pupilDilate;  // extra pupil size multiplier while startled (0 = 1)
  float fireStretch;  // radial fire: radial noise frequency multiplier (0 = 1; <1 = long streaks)
  EyeVariant right;   // right-panel overrides
};

// Sub-eye layout used when cluster > 0: offset from the panel centre (px, left panel;
// x is mirrored for the right panel) and scale of every eye dimension.
struct ClusterEye {
  float x, y, scale;
};
constexpr int kMaxClusterEyes = 5;
const ClusterEye* clusterLayout(int count);

const ThemeSpec* themeById(const char* id);
const ThemeSpec* themeAt(int index);
int themeCount();

}  // namespace eyes
