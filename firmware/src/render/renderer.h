// Procedural eye renderer. Portable C++ (used on-device and by tools/preview).
#pragma once
#include <stdint.h>

#include "theme.h"

namespace eyes {

constexpr int kSize = 240;

// Everything that varies per frame, per eye. Produced by EyeController.
struct EyeState {
  float gazeX = 0, gazeY = 0;  // -1..1
  float pupil = 0.5f;          // 0..1 dilation
  float lidTop = 0;            // 0 open .. 1 fully closed
  float lidBottom = 0;
  float lidSlant = 0;          // -1..1, +1 = angry (inner corner lowered)
  float glow = 1;              // multiplier for glow/fire/emissive parts
  float time = 0;              // seconds
  bool mirror = false;         // true for the right-hand eye
};

// Static per-theme layers (iris fibres, sclera veins, machined rings), built once per theme
// and shared by both eyes. Lives in PSRAM on the device.
class ThemeCache {
 public:
  ~ThemeCache();
  void build(const ThemeSpec* theme);
  const ThemeSpec* theme() const { return theme_; }

 private:
  friend class Renderer;
  static constexpr int kIrisAngles = 1024;
  static constexpr int kVeinSize = 320;  // covers the screen plus gaze travel
  static constexpr int kVeinOffset = 40;
  static constexpr int kRingSteps = 4;   // samples per px
  static constexpr int kRingLen = 400 * kRingSteps;

  void release();

  const ThemeSpec* theme_ = nullptr;
  int irisRadii_ = 0;
  uint8_t* fibre_ = nullptr;   // [radius][angle] fibre noise 0..255
  uint8_t* veins_ = nullptr;   // [y][x] vein alpha 0..255
  float* rings_ = nullptr;     // [r * kRingSteps] brightness factor
};

class Renderer {
 public:
  Renderer();
  ~Renderer();
  void setCache(const ThemeCache* cache) { cache_ = cache; }

  // Prepare per-frame constants and the low-res fire field. Call once per frame before
  // renderRows (on the core that renders this eye).
  void begin(const EyeState& s);

  // Shade rows [y0, y1) into out (width kSize). RGB565; byteSwap for SPI panels.
  void renderRows(int y0, int y1, uint16_t* out, bool byteSwap) const;

 private:
  struct Col {
    float r, g, b;
  };
  Col shade(int px, int py) const;
  float fireAt(float fx, float fy, float ux, float uy, float r) const;
  void buildFireField();

  static constexpr int kFireGrid = 4;  // px per cell (rising fire)
  static constexpr int kFireCells = kSize / kFireGrid + 2;
  static constexpr int kFireAngles = 96;
  static constexpr int kFireRadii = 48;

  const ThemeCache* cache_ = nullptr;
  const ThemeSpec* t_ = nullptr;
  EyeState s_;
  float cx_ = 0, cy_ = 0;
  float squashX_ = 1, squashY_ = 1;
  float pupilR_ = 0;
  float glowK_ = 0;
  float emissive_ = 1;
  int swirlOffset_ = 0;
  float fireR0_ = 0, fireRStep_ = 1;
  float* fire_ = nullptr;  // rising: [kFireCells^2], radial: [kFireRadii][kFireAngles]
  float lidTop_[kSize], lidBot_[kSize];
};

}  // namespace eyes
