// Theme definitions: every look is data, shaded procedurally by Renderer.
#pragma once
#include <stdint.h>

namespace eyes {

struct Rgb {
  float r, g, b;
};

enum class PupilShape : uint8_t { Round, Slit, None };
enum class FireMode : uint8_t { None, Rising, Radial };

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
};

const ThemeSpec* themeById(const char* id);
const ThemeSpec* themeAt(int index);
int themeCount();

}  // namespace eyes
