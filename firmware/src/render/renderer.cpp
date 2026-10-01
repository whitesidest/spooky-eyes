#include "renderer.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "noise.h"

#if !defined(ESP_PLATFORM)
#define HOT
#endif

#if defined(ESP_PLATFORM)
#include <esp_attr.h>
#include <esp_heap_caps.h>
// The per-pixel path runs from IRAM so the two render cores don't thrash the shared flash cache.
#define HOT IRAM_ATTR
#pragma GCC optimize("O3,fast-math")
#endif

namespace eyes {
namespace {

constexpr float kCentre = kSize / 2.0f;
constexpr float kScreenR = kSize / 2.0f;
constexpr float kPi = 3.14159265f;
constexpr float kTwoPi = 6.2831853f;
constexpr float kScleraSpiralK = kTwoPi / 16.0f;  // sclera spiral pitch: one turn every 16 px

// Small, hot per-frame buffers: prefer fast internal RAM.
void* fastAlloc(size_t bytes) {
#if defined(ESP_PLATFORM)
  void* p = heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  return p ? p : heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
#else
  return malloc(bytes);
#endif
}

void* bigAlloc(size_t bytes) {
#if defined(ESP_PLATFORM)
  void* p = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
  return p ? p : malloc(bytes);
#else
  return malloc(bytes);
#endif
}

// The ESP32-S3 FPU has no divide or square-root instruction, so GCC emits slow software calls
// (__divsf3, sqrtf). These multiply-only versions (bit-trick seed + Newton steps) are accurate to
// ~1e-6 relative, far below what an RGB565 pixel can show.
inline float fastRecip(float x) {
  union {
    float f;
    uint32_t i;
  } u{fabsf(x)};
  u.i = 0x7EF311C3u - u.i;
  float y = u.f, a = fabsf(x);
  y = y * (2.0f - a * y);
  y = y * (2.0f - a * y);
  y = y * (2.0f - a * y);
  return x < 0 ? -y : y;
}
inline float fastRsqrt(float x) {
  union {
    float f;
    uint32_t i;
  } u{x};
  u.i = 0x5F375A86u - (u.i >> 1);
  float y = u.f;
  y = y * (1.5f - 0.5f * x * y * y);
  y = y * (1.5f - 0.5f * x * y * y);
  return y;
}
inline float fastSqrt(float x) { return x > 0 ? x * fastRsqrt(x) : 0.0f; }
inline float fdiv(float a, float b) { return a * fastRecip(b); }

// sin() from a 256-entry table with linear interpolation (any angle).
struct SinTable {
  float v[257];
  SinTable() {
    for (int i = 0; i <= 256; ++i) v[i] = sinf(i * 6.2831853f / 256.0f);
  }
};
const SinTable kSin;
inline float fastSin(float a) {
  float f = a * (256.0f / 6.2831853f);
  float fl = floorf(f);
  int i = ((int)fl) & 255;
  return kSin.v[i] + (kSin.v[i + 1] - kSin.v[i]) * (f - fl);
}

inline float clamp01(float v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
inline float lerp(float a, float b, float t) { return a + (b - a) * t; }
inline float smoothstep(float e0, float e1, float x) {
  // Most call sites have a compile-time width: fold its reciprocal instead of computing it per pixel.
  const float w = e1 - e0;
  const float inv = __builtin_constant_p(w) ? 1.0f / w : fastRecip(w);
  return smooth01(clamp01((x - e0) * inv));
}

// exp(-x) for x >= 0 via a lerped table; plenty for shading.
struct ExpTable {
  static constexpr int kN = 512;
  static constexpr float kMax = 10.0f;
  float v[kN + 1];
  ExpTable() {
    for (int i = 0; i <= kN; ++i) v[i] = expf(-kMax * i / kN);
  }
};
const ExpTable kExp;
inline float expNeg(float x) {
  if (x <= 0) return 1;
  if (x >= ExpTable::kMax) return 0;
  float f = x * (ExpTable::kN / ExpTable::kMax);
  int i = (int)f;
  return kExp.v[i] + (kExp.v[i + 1] - kExp.v[i]) * (f - i);
}

// Max error ~0.001 rad.
inline float fastAtan2(float y, float x) {
  float ax = fabsf(x), ay = fabsf(y);
  float mx = ax > ay ? ax : ay, mn = ax > ay ? ay : ax;
  float a = mx > 0 ? mn * fastRecip(mx) : 0;
  float s = a * a;
  float r = ((-0.0464964749f * s + 0.15931422f) * s - 0.327622764f) * s * a + a;
  if (ay > ax) r = 1.57079637f - r;
  if (x < 0) r = kPi - r;
  if (y < 0) r = -r;
  return r;
}

uint8_t kBayer[4][4] =  // non-const: keep in DRAM for the IRAM render loop
    {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};

}  // namespace

// ---------------------------------------------------------------- ThemeCache

ThemeCache::~ThemeCache() { release(); }

void ThemeCache::release() {
  free(fibre_);
  free(veins_);
  free(rings_);
  fibre_ = veins_ = nullptr;
  rings_ = nullptr;
}

void ThemeCache::build(const ThemeSpec* theme) {
  release();
  theme_ = theme;
  const ThemeSpec& t = *theme;

  if (t.striation > 0) {
    irisRadii_ = (int)t.irisRadius + 2;
    fibre_ = (uint8_t*)bigAlloc((size_t)irisRadii_ * kIrisAngles);
    const float f = t.striationFreq / kTwoPi;
    for (int ri = 0; ri < irisRadii_; ++ri) {
      float r = ri + 0.5f;
      for (int ai = 0; ai < kIrisAngles; ++ai) {
        float a = ai * kTwoPi / kIrisAngles - kPi;
        float ux = cosf(a), uy = sinf(a);
        float n = vnoise(ux * f, uy * f, r * 0.06f) * 0.65f +
                  vnoise(ux * f * 2.3f, uy * f * 2.3f, r * 0.1f + 4.0f) * 0.35f;
        fibre_[ri * kIrisAngles + ai] = (uint8_t)(clamp01(n) * 255.0f + 0.5f);
      }
    }
  }

  if (t.veins > 0) {
    veins_ = (uint8_t*)bigAlloc(kVeinSize * kVeinSize);
    for (int y = 0; y < kVeinSize; ++y) {
      for (int x = 0; x < kVeinSize; ++x) {
        float vx = x - kVeinOffset + 0.5f, vy = y - kVeinOffset + 0.5f;
        float n = vnoise(vx * 0.045f, vy * 0.045f, 3.7f) * 0.7f + vnoise(vx * 0.11f, vy * 0.11f, 9.1f) * 0.3f;
        float ridge = 1.0f - fabsf(2.0f * n - 1.0f);
        for (int k = 0; k < 4; ++k) ridge *= ridge;
        veins_[y * kVeinSize + x] = (uint8_t)(clamp01(ridge * t.veins) * 255.0f + 0.5f);
      }
    }
  }

  if (t.rings > 0) {
    rings_ = (float*)bigAlloc(kRingLen * sizeof(float));
    for (int i = 0; i < kRingLen; ++i) {
      float r = (float)i / kRingSteps;
      float band = 0.5f + 0.5f * sinf(r * 0.55f);
      float groove = smoothstep(0.85f, 1.0f, 0.5f + 0.5f * sinf(r * 0.16f + 1.3f));
      rings_[i] = (0.55f + 0.45f * band) * (1.0f - 0.6f * groove);
    }
  }
}

// ------------------------------------------------------------------ Renderer

Renderer::Renderer() {
  int cells = kFireCells * kFireCells;
  int polar = kFireRadii * kFireAngles;
  fire_ = (float*)fastAlloc(sizeof(float) * (cells > polar ? cells : polar));
  fireI_ = (float*)fastAlloc(sizeof(float) * cells);
  base_ = (Texel*)bigAlloc(sizeof(Texel) * kBaseMax * kBaseMax);
}

Renderer::~Renderer() {
  free(fire_);
  free(fireI_);
  free(base_);
}

void Renderer::begin(const EyeState& s) {
  const ThemeSpec* theme = cache_->theme();
  if (halfRes_ && base_ && (theme != bakedTheme_ || s.mirror != bakedMirror_)) bake(theme, s.mirror);
  setupFrame(s);
  const ThemeSpec& t = *t_;
  fast_ = halfRes_ && base_ && bakedTheme_ == theme && t.cluster == 0;
  if (t.fire != FireMode::None && t.fireAmount > 0) buildFireField();
}

void Renderer::bake(const ThemeSpec* theme, bool mirror) {
  EyeState st;
  st.mirror = mirror;
  st.pupil = 0.5f;
  st.glow = 1;
  setupFrame(st);
  const ThemeSpec& t = *t_;
  int n = kSize / 2 + 2 * (int)ceilf(t.gazeRange * 0.5f) + 6;
  baseN_ = n > kBaseMax ? kBaseMax : n;
  baseHalf_ = baseN_ * 0.5f;
  bakeMode_ = true;
  for (int j = 0; j < baseN_; ++j) {
    for (int i = 0; i < baseN_; ++i) {
      float dx = (i + 0.5f - baseHalf_) * 2.0f, dy = (j + 0.5f - baseHalf_) * 2.0f;
      lastIrisA_ = 0;
      Col c = shade(0, 0, kCentre + dx, kCentre + dy);
      Texel& tx = base_[j * baseN_ + i];
      tx.r = (uint8_t)(clamp01(c.r) * 255.0f + 0.5f);
      tx.g = (uint8_t)(clamp01(c.g) * 255.0f + 0.5f);
      tx.b = (uint8_t)(clamp01(c.b) * 255.0f + 0.5f);
      tx.iris = (uint8_t)(clamp01(lastIrisA_) * 255.0f + 0.5f);
      // Polar coordinates around the iris centre, so per-frame layers skip atan2/sqrt.
      float rr = sqrtf(dx * dx + dy * dy);
      tx.ang = (uint16_t)((atan2f(dy, dx) + kPi) * (65535.0f / kTwoPi));
      tx.rad = (uint16_t)(rr * 64.0f > 65535.0f ? 65535.0f : rr * 64.0f);
    }
  }
  bakeMode_ = false;
  bakedTheme_ = theme;
  bakedMirror_ = mirror;
}

void Renderer::setupFrame(const EyeState& s) {
  s_ = s;
  // Shade from a RAM copy: the theme table lives in flash, and both cores fight over the flash cache.
  themeCopy_ = *cache_->theme();
  t_ = &themeCopy_;
  const ThemeSpec& t = *t_;
  cx_ = kCentre + s.gazeX * t.gazeRange;
  cy_ = kCentre + s.gazeY * t.gazeRange;
  // Looking sideways foreshortens the iris along the gaze axis.
  squashX_ = 1.0f + 0.3f * fabsf(s.gazeX);
  squashY_ = 1.0f + 0.3f * fabsf(s.gazeY);
  pupilR_ = lerp(t.pupilMin, t.pupilMax, clamp01(s.pupil));
  float pulse = t.glowPulse > 0 ? 0.78f + 0.22f * sinf(kTwoPi * t.glowPulse * s.time) : 1.0f;
  glowK_ = s.glow * pulse;
  emissive_ = t.glowAmount > 0 ? lerp(0.25f, 1.0f, clamp01(s.glow)) : 1.0f;

  // Per-eye colour set: the right panel may override the theme.
  irisInner_ = t.irisInner;
  irisOuter_ = t.irisOuter;
  pupilColor_ = t.pupilColor;
  glowColor_ = t.glowColor;
  glowAmount_ = t.glowAmount;
  glowInvR2_ = t.glowRadius > 0 ? 1.0f / (t.glowRadius * t.glowRadius) : 0.0f;
  haze_ = 0;
  spiralCx_ = s.mirror ? -t.scleraSpiralX : t.scleraSpiralX;  // "outward" flips on the right panel
  if (s.mirror && t.right.enabled) {
    const EyeVariant& v = t.right;
    irisInner_ = v.irisInner;
    irisOuter_ = v.irisOuter;
    pupilColor_ = v.pupilColor;
    glowColor_ = v.glowColor;
    if (v.pupilScale > 0) pupilR_ *= v.pupilScale;
    if (v.glowScale > 0) glowAmount_ *= v.glowScale;
    haze_ = clamp01(v.haze);
  }

  // Sub-eye cluster: centres slide a little with the gaze, sizes come from the layout.
  clusterN_ = 0;
  if (t.cluster > 0) {
    const ClusterEye* lay = clusterLayout(t.cluster);
    clusterN_ = t.cluster > kMaxClusterEyes ? kMaxClusterEyes : t.cluster;
    for (int i = 0; i < clusterN_; ++i) {
      clusterX_[i] = cx_ + (s.mirror ? -lay[i].x : lay[i].x);
      clusterY_[i] = cy_ + lay[i].y;
      clusterS_[i] = lay[i].scale;
      clusterInvS_[i] = 1.0f / lay[i].scale;
      clusterInvS2_[i] = clusterInvS_[i] * clusterInvS_[i];
    }
  }
  // Twinkle phases for the sparkle highlights (different per eye).
  {
    float ph = s.mirror ? 1.9f : 0.0f;
    sparkleK_[0] = 0.55f + 0.45f * sinf(s.time * 2.7f + ph);
    sparkleK_[1] = 0.55f + 0.45f * sinf(s.time * 3.9f + 2.1f + ph);
  }
  float swirl = fmodf(t.irisSwirl * s.time, kTwoPi);
  swirlOffset_ = (int)(swirl * ThemeCache::kIrisAngles / kTwoPi);
  hueOn_ = t.hueSpin != 0;
  if (hueOn_) {
    // Rotate hue about the grey axis.
    float a = fmodf(t.hueSpin * s.time, 1.0f) * kTwoPi;
    float c = cosf(a), sn = sinf(a) * 0.57735027f, k = (1.0f - c) / 3.0f;
    float m0 = c + k, m1 = k - sn, m2 = k + sn;
    const float m[9] = {m0, m1, m2, m2, m0, m1, m1, m2, m0};
    memcpy(hue_, m, sizeof m);
  }

  if (t.lids) {
    // Lids follow the gaze a little, like real ones.
    float topBase = lerp(-40.0f, 130.0f, clamp01(s.lidTop)) + (cy_ - kCentre) * 0.45f;
    float botBase = lerp(280.0f, 130.0f, clamp01(s.lidBottom)) + (cy_ - kCentre) * 0.25f;
    for (int x = 0; x < kSize; ++x) {
      float xn = (x + 0.5f - kCentre) / kScreenR;
      float bulge = 1.0f - xn * xn;
      float inner = s.mirror ? -xn : xn;  // +1 toward the nose
      lidTop_[x] = topBase + 38.0f * bulge + s.lidSlant * 34.0f * inner;
      lidBot_[x] = botBase - 30.0f * bulge + s.lidSlant * 6.0f * inner;
    }
  }
  specInvA_ = t.irisRadius > 0 ? 1.0f / ((t.irisRadius * 0.13f) * (t.irisRadius * 0.13f)) : 0.0f;
  specInvB_ = t.irisRadius > 0 ? 1.0f / ((t.irisRadius * 0.06f) * (t.irisRadius * 0.06f)) : 0.0f;
}

void Renderer::buildFireField() {
  // Refresh half of the field each frame (alternating lines): flames still move every frame,
  // each line updates at half rate, and the cost halves. Full rebuild when the theme changes.
  const bool full = fireTheme_ != t_->id;
  fireTheme_ = t_->id;
  fireParity_ ^= 1;
  const int firstLine = full ? 0 : fireParity_, lineStep = full ? 1 : 2;
  const ThemeSpec& t = *t_;
  const float z = s_.mirror ? 17.0f : 0.0f;  // different flames per eye
  if (t.fire == FireMode::Radial) {
    fireR0_ = t.fireInner - 12.0f;
    fireRStep_ = (t.fireOuter + 10.0f - fireR0_) / (kFireRadii - 1);
    fireInvStep_ = 1.0f / fireRStep_;
    const float fs = t.fireScale;
    const float rs = fs * 0.09f * (t.fireStretch > 0 ? t.fireStretch : 1.0f);
    for (int ai = firstLine; ai < kFireAngles; ai += lineStep) {
      float a = ai * kTwoPi / kFireAngles - kPi;
      float ux = cosf(a), uy = sinf(a);
      for (int ri = 0; ri < kFireRadii; ++ri) {
        float r = fireR0_ + ri * fireRStep_;
        fire_[ri * kFireAngles + ai] =
            fbm3Fix(ux * fs * 3.0f + 11.0f + z, uy * fs * 3.0f, r * rs - s_.time * t.fireSpeed);
      }
    }
  } else {
    // Pit flames are stretched vertically into tall tongues.
    const float sy = t.fire == FireMode::Pit ? 0.45f : 1.0f;
    for (int gy = firstLine; gy < kFireCells; gy += lineStep) {
      for (int gx = 0; gx < kFireCells; ++gx) {
        float fx = gx * kFireGrid, fy = gy * kFireGrid;
        fire_[gy * kFireCells + gx] =
            fbm3Fix(fx * t.fireScale + z, (fy * t.fireScale + s_.time * t.fireSpeed) * sy, s_.time * 0.35f);
      }
    }
    // Shape the flames once per cell (distance, bands, palette input) so pixels only interpolate.
    for (int gy = 0; gy < kFireCells; ++gy) {
      for (int gx = 0; gx < kFireCells; ++gx) {
        const float fx = gx * kFireGrid, fy = gy * kFireGrid;
        const float dx = fx - cx_, dy = fy - cy_;
        const int i = gy * kFireCells + gx;
        fireI_[i] = fireGridValue(fx, fy, fastSqrt(dx * dx + dy * dy), fire_[i]);
      }
    }
  }
}

HOT float Renderer::fireAt(float fx, float fy, float ux, float uy, float r) const {
  const ThemeSpec& t = *t_;
  if (t.fire == FireMode::Radial) return fireRadialAt((fastAtan2(uy, ux) + kPi) * (1.0f / kTwoPi), r);
  float gx = fx / kFireGrid, gy = fy / kFireGrid;
  int x0 = (int)gx, y0 = (int)gy;
  float tx = gx - x0, ty = gy - y0;
  const float* p = fire_ + y0 * kFireCells + x0;
  return fireGridAt(fx, fy, r, p, tx, ty);
}

// Radial flames at a polar position: angle as a 0..1 fraction of a turn, r in px from the iris centre.
HOT float Renderer::fireRadialAt(float a01, float r) const {
  const ThemeSpec& t = *t_;
  float n;
  {
    float band = smoothstep(t.fireInner - 12.0f, t.fireInner + 6.0f, r) *
                 (1.0f - smoothstep(t.fireOuter - 25.0f, t.fireOuter + 10.0f, r));
    if (band <= 0) return 0;
    float af = a01 * kFireAngles;
    float rf = (r - fireR0_) * fireInvStep_;
    if (rf < 0) rf = 0;
    if (rf > kFireRadii - 1.001f) rf = kFireRadii - 1.001f;
    int a0 = (int)af, r0 = (int)rf;
    float ta = af - a0, tr = rf - r0;
    a0 %= kFireAngles;
    int a1 = (a0 + 1) % kFireAngles;
    const float* row0 = fire_ + r0 * kFireAngles;
    const float* row1 = row0 + kFireAngles;
    float n0 = lerp(row0[a0], row0[a1], ta), n1 = lerp(row1[a0], row1[a1], ta);
    n = lerp(n0, n1, tr);
    return clamp01(band * (n * 2.3f - 0.85f) + band * 0.12f);
  }
}

// Rising / pit flames from the screen-space grid (p = top-left cell, tx/ty = bilinear weights).
// Flame intensity at a grid point from its noise value n (evaluated once per cell per frame).
float Renderer::fireGridValue(float fx, float fy, float r, float n) const {
  const ThemeSpec& t = *t_;
  if (t.fire == FireMode::Pit) {
    // Source sits low in the socket; flames reach far upward and barely downward.
    float dx = fx - cx_, dy = fy - (cy_ + 38.0f);
    float v = dy < 0 ? dy * 0.5f : dy * 1.7f;
    float rr = fastSqrt(dx * dx + v * v);
    float base = 1.0f - smoothstep(18.0f, t.fireOuter, rr);
    if (base <= 0) return 0;
    // Ember bed: a wide, always-hot pool at the bottom of the socket.
    float ex = dx * 0.55f, ey = dy < 0 ? dy * 1.2f : dy * 1.8f;
    float er = fastSqrt(ex * ex + ey * ey);
    float core = 0.75f * (1.0f - smoothstep(6.0f, 46.0f, er)) * (0.7f + 0.3f * n);
    return clamp01(base * base * 1.6f * (n * 2.5f - 0.75f) + core);
  }
  float dx = fx - cx_, dy = fy - cy_;
  float up = dy < 0 ? dy * 0.45f : dy;
  float rr = fastSqrt(dx * dx + up * up);
  float base = smoothstep(t.fireInner - 8.0f, t.fireInner + 4.0f, r) * (1.0f - smoothstep(t.fireInner, t.fireOuter, rr));
  if (base <= 0) return 0;
  return clamp01(base * (n * 2.2f - 0.45f));
}

// Rising / pit flames: bilinear lookup in the per-frame intensity grid (p = top-left noise cell).
HOT float Renderer::fireGridAt(float, float, float, const float* p, float tx, float ty) const {
  const float* q = fireI_ + (p - fire_);
  return lerp(lerp(q[0], q[1], tx), lerp(q[kFireCells], q[kFireCells + 1], tx), ty);
}

// Signed distance (px, <0 inside) to the pupil shape; dx/dy relative to the iris centre.
HOT float Renderer::pupilEdgeAt(float dx, float dy, float r) const {
  const ThemeSpec& t = *t_;
  float edge;
  if (t.pupilShape == PupilShape::Round) {
    edge = r - pupilR_;
  } else if (t.pupilShape == PupilShape::Bar) {
    // Rounded horizontal box: half-height pupilR, half-width slitHeight * irisRadius.
    float hw = t.slitHeight * t.irisRadius, hh = pupilR_;
    float qx = fabsf(dx) - hw + hh, qy = fabsf(dy);
    float ox = qx > 0 ? qx : 0;
    edge = (qx > 0 ? fastSqrt(ox * ox + qy * qy) : qy) - hh;
  } else if (t.pupilShape == PupilShape::Heart) {
    // Inigo Quilez's heart SDF; unit heart spans y 0..~1.1 with y up.
    const float sc = pupilR_ * 1.7f;
    const float isc = fastRecip(sc);
    float x = fabsf(dx) * isc, y = -dy * isc + 0.55f;
    float d;
    if (x + y > 1.0f) {
      float ax = x - 0.25f, ay = y - 0.75f;
      d = fastSqrt(ax * ax + ay * ay) - 0.35355339f;
    } else {
      float bx = x, by = y - 1.0f;
      float m = 0.5f * (x + y > 0 ? x + y : 0);
      float cx = x - m, cy = y - m;
      float d2 = bx * bx + by * by, c2 = cx * cx + cy * cy;
      d = fastSqrt(d2 < c2 ? d2 : c2) * (x - y > 0 ? 1.0f : -1.0f);
    }
    edge = d * sc;
  } else if (t.pupilShape == PupilShape::Triangle) {
    // Inigo Quilez's equilateral triangle SDF, pointing up; pupilR = circumradius-ish.
    const float k = 1.7320508f, rr = pupilR_;
    float x = fabsf(dx) - rr, y = -dy + rr / k;
    if (x + k * y > 0) {
      float nx = (x - k * y) * 0.5f, ny = (-k * x - y) * 0.5f;
      x = nx;
      y = ny;
    }
    float c = x < -2.0f * rr ? -2.0f * rr : (x > 0 ? 0 : x);
    x -= c;
    edge = -fastSqrt(x * x + y * y) * (y > 0 ? 1.0f : -1.0f);
  } else if (t.pupilShape == PupilShape::Star) {
    // Inigo Quilez's five-point star SDF; pupilR = outer radius.
    const float k1x = 0.809016994f, k1y = -0.587785252f;
    const float rr = pupilR_, rf = 0.45f;
    float x = fabsf(dx), y = -dy;
    float d1 = k1x * x + k1y * y;
    if (d1 > 0) {
      x -= 2.0f * d1 * k1x;
      y -= 2.0f * d1 * k1y;
    }
    float d2 = -k1x * x + k1y * y;
    if (d2 > 0) {
      x += 2.0f * d2 * k1x;
      y -= 2.0f * d2 * k1y;
    }
    x = fabsf(x);
    y -= rr;
    float bax = rf * -k1y, bay = rf * k1x - 1.0f;
    float h = fdiv(x * bax + y * bay, bax * bax + bay * bay);
    h = h < 0 ? 0 : (h > rr ? rr : h);
    float px2 = x - bax * h, py2 = y - bay * h;
    edge = fastSqrt(px2 * px2 + py2 * py2) * ((y * bax - x * bay) > 0 ? 1.0f : -1.0f);
  } else if (t.pupilShape == PupilShape::Clover) {
    // Shamrock: three leaves (circles) around the centre plus a short stem.
    const float leaf = pupilR_ * 0.55f, off = pupilR_ * 0.48f;
    float ax = dx, ay = dy + off;  // top leaf
    float bx = dx - off * 0.866f, by = dy - off * 0.5f;
    float cx2 = dx + off * 0.866f, cy2 = dy - off * 0.5f;
    float da = fastSqrt(ax * ax + ay * ay) - leaf;
    float db = fastSqrt(bx * bx + by * by) - leaf;
    float dc = fastSqrt(cx2 * cx2 + cy2 * cy2) - leaf;
    edge = da < db ? da : db;
    if (dc < edge) edge = dc;
    // Stem: thin rounded bar hanging down.
    float sx = dx + 0.32f * (dy - off * 0.3f), sy = dy - off * 0.3f;
    float qx = fabsf(sx) - pupilR_ * 0.11f, qy = fabsf(sy) - pupilR_ * 0.62f;
    float ox = qx > 0 ? qx : 0, oy = qy > 0 ? qy : 0;
    float m = qx > qy ? qx : qy;
    float ds = fastSqrt(ox * ox + oy * oy) + (m < 0 ? m : 0);
    if (sy > 0 && ds < edge) edge = ds;
  } else if (t.pupilShape == PupilShape::Cross) {
    // Two bars rotated 45 degrees: an "X". pupilR = half-length, slitHeight = thickness ratio.
    const float u = (dx + dy) * 0.70710678f, v = (dx - dy) * 0.70710678f;
    const float hl = pupilR_, hw = pupilR_ * (t.slitHeight > 0 ? t.slitHeight : 0.22f);
    float qx = fabsf(u) - hl, qy = fabsf(v) - hw;
    float ox = qx > 0 ? qx : 0, oy = qy > 0 ? qy : 0;
    float m = qx > qy ? qx : qy;
    float d1 = fastSqrt(ox * ox + oy * oy) + (m < 0 ? m : 0);
    qx = fabsf(v) - hl;
    qy = fabsf(u) - hw;
    ox = qx > 0 ? qx : 0;
    oy = qy > 0 ? qy : 0;
    m = qx > qy ? qx : qy;
    float d2 = fastSqrt(ox * ox + oy * oy) + (m < 0 ? m : 0);
    edge = d1 < d2 ? d1 : d2;
  } else {
    float q = fdiv(dy, t.slitHeight * t.irisRadius);
    float halfW = pupilR_ * (1.0f - q * q);
    edge = fabsf(dx) - (halfW > 0 ? halfW : -1.0f);
  }
  return edge;
}

HOT Renderer::Col Renderer::shade(int px, int py, float fx, float fy) const {
  const ThemeSpec& t = *t_;
  const float sdx = fx - kCentre, sdy = fy - kCentre;
  const float rScreen = fastSqrt(sdx * sdx + sdy * sdy);
  const float rim = 1.0f - 0.45f * smoothstep(70.0f, 121.0f, rScreen);

  // --- Eyelids first: fully covered pixels skip the eye entirely ---
  float lidA = 0, lidEdgeY = 0, shadow = 1;
  if (t.lids && !bakeMode_) {
    const float yTop = lidTop_[px], yBot = lidBot_[px];
    float topA = 1.0f - smoothstep(yTop - 0.7f, yTop + 0.7f, fy);
    float botA = smoothstep(yBot - 0.7f, yBot + 0.7f, fy);
    lidA = topA > botA ? topA : botA;
    lidEdgeY = topA > botA ? yTop : yBot;
    if (fy > yTop) shadow *= 1.0f - 0.55f * expNeg((fy - yTop) / 9.0f);
    if (fy < yBot) shadow *= 1.0f - 0.35f * expNeg((yBot - fy) / 7.0f);
  }
  Col lid = {0, 0, 0};
  if (lidA > 0) {
    float dist = fabsf(fy - lidEdgeY);
    float k = 0.7f + 0.3f * smoothstep(0.0f, 45.0f, dist);
    float e = smoothstep(1.5f, 5.0f, dist);
    lid.r = lerp(t.lidEdge.r, t.lidColor.r * k, e) * rim;
    lid.g = lerp(t.lidEdge.g, t.lidColor.g * k, e) * rim;
    lid.b = lerp(t.lidEdge.b, t.lidColor.b * k, e) * rim;
    if (lidA >= 1.0f) return lid;
  }

  // Iris-relative coordinates (foreshortened). With a cluster, every pixel belongs to the
  // nearest sub-eye and is shaded in that eye's scaled local space.
  float dx = (fx - cx_) * squashX_, dy = (fy - cy_) * squashY_;
  if (clusterN_ > 0) {
    int best = 0;
    float bestD = 1e30f;
    for (int i = 0; i < clusterN_; ++i) {
      float ex = fx - clusterX_[i], ey = fy - clusterY_[i];
      float d = (ex * ex + ey * ey) * clusterInvS2_[i];
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    const float invS = clusterInvS_[best];
    dx = (fx - clusterX_[best]) * invS;
    dy = (fy - clusterY_[best]) * invS;
  }
  const float r = fastSqrt(dx * dx + dy * dy) + 1e-4f;
  const float inv = fastRecip(r);
  const float ux = dx * inv, uy = dy * inv;

  // --- Sclera ---
  float e = smoothstep(30.0f, 118.0f, rScreen);
  Col col = {lerp(t.sclera.r, t.scleraEdge.r, e), lerp(t.sclera.g, t.scleraEdge.g, e),
             lerp(t.sclera.b, t.scleraEdge.b, e)};
  if (cache_->veins_ && r > t.irisRadius + 20) {
    int vx = (int)(fx - (cx_ - kCentre) * 0.6f) + ThemeCache::kVeinOffset;
    int vy = (int)(fy - (cy_ - kCentre) * 0.6f) + ThemeCache::kVeinOffset;
    if (vx >= 0 && vy >= 0 && vx < ThemeCache::kVeinSize && vy < ThemeCache::kVeinSize) {
      float v = cache_->veins_[vy * ThemeCache::kVeinSize + vx] * (1.0f / 255.0f) *
                smoothstep(t.irisRadius + 20, 115.0f, r);
      col.r = lerp(col.r, 0.62f, v);
      col.g = lerp(col.g, 0.08f, v);
      col.b = lerp(col.b, 0.08f, v);
    }
  }
  if (cache_->rings_) {
    int ri = (int)(r * ThemeCache::kRingSteps);
    if (ri >= ThemeCache::kRingLen) ri = ThemeCache::kRingLen - 1;
    float sheen = 0.55f + 0.45f * (-uy * 0.6f + ux * 0.2f);
    float k = lerp(1.0f, cache_->rings_[ri] * sheen, t.rings);
    col.r *= k;
    col.g *= k;
    col.b *= k;
  }
  // Hollow socket: a flat dark ring the iris sits inside (the iris is drawn over its centre).
  if (t.socket > 0) {
    const float ro = t.irisRadius + t.socket;
    float sa = 1.0f - smoothstep(ro - 1.5f, ro + 1.5f, r);
    if (sa > 0) {
      col.r = lerp(col.r, t.socketColor.r, sa);
      col.g = lerp(col.g, t.socketColor.g, sa);
      col.b = lerp(col.b, t.socketColor.b, sa);
    }
  }
  // Painted sclera spiral (a "cheek" beside the socket): static, so it is baked with the eyeball.
  if (t.scleraSpiral > 0) {
    const float r0 = t.irisRadius + t.socket;
    if (r > r0) {
      const float sx = fx - cx_ - spiralCx_, sy = fy - cy_ - t.scleraSpiralY;
      const float rs = fastSqrt(sx * sx + sy * sy);
      float band = 0.5f + 0.5f * fastSin(fastAtan2(sy, sx) + rs * kScleraSpiralK);
      float w = t.scleraSpiral * smoothstep(0.38f, 0.62f, band) * smoothstep(r0, r0 + 5.0f, r);
      if (rs < 5.0f) w = t.scleraSpiral;  // solid dot at the spiral's heart
      if (t.scleraSpiralRadius > 0) w *= 1.0f - smoothstep(t.scleraSpiralRadius - 3.0f, t.scleraSpiralRadius, rs);
      col.r = lerp(col.r, t.scleraSpiralColor.r, w);
      col.g = lerp(col.g, t.scleraSpiralColor.g, w);
      col.b = lerp(col.b, t.scleraSpiralColor.b, w);
    }
  }

  // --- Iris ---
  const float irisA = 1.0f - smoothstep(t.irisRadius - 1.0f, t.irisRadius + 1.0f, r);
  if (irisA > 0) {
    float span = t.irisRadius - pupilR_;
    float tr = clamp01((r - pupilR_) * fastRecip(span > 1 ? span : 1));
    if (t.spiral > 0) {
      float a = fastAtan2(uy, ux);
      float band = 0.5f + 0.5f * sinf(a * 2.0f + r * 0.16f - s_.time * t.spiralSpeed);
      tr = lerp(tr, smoothstep(0.3f, 0.7f, band), t.spiral);
    }
    Col iris = {lerp(irisInner_.r, irisOuter_.r, tr), lerp(irisInner_.g, irisOuter_.g, tr),
                lerp(irisInner_.b, irisOuter_.b, tr)};
    if (cache_->fibre_) {
      int ri = (int)r;
      if (ri >= cache_->irisRadii_) ri = cache_->irisRadii_ - 1;
      int ai = ((int)((fastAtan2(uy, ux) + kPi) * (ThemeCache::kIrisAngles / kTwoPi)) + swirlOffset_) &
               (ThemeCache::kIrisAngles - 1);
      float n = cache_->fibre_[ri * ThemeCache::kIrisAngles + ai] * (1.0f / 255.0f);
      float k = 1.0f - t.striation * 0.6f + t.striation * 1.1f * n;
      iris.r *= k;
      iris.g *= k;
      iris.b *= k;
    }
    if (hueOn_) {
      Col h = {hue_[0] * iris.r + hue_[1] * iris.g + hue_[2] * iris.b,
               hue_[3] * iris.r + hue_[4] * iris.g + hue_[5] * iris.b,
               hue_[6] * iris.r + hue_[7] * iris.g + hue_[8] * iris.b};
      iris = h;
    }
    float lim = smoothstep(t.irisRadius - t.limbusWidth, t.irisRadius, r) * 0.9f;
    col.r = lerp(col.r, lerp(iris.r, t.limbus.r, lim) * emissive_, irisA);
    col.g = lerp(col.g, lerp(iris.g, t.limbus.g, lim) * emissive_, irisA);
    col.b = lerp(col.b, lerp(iris.b, t.limbus.b, lim) * emissive_, irisA);
  }
  if (bakeMode_) {
    // Baking the static layers: everything below is per-frame.
    lastIrisA_ = irisA > 0 ? irisA : 0;
    return col;
  }

  // --- Fire ---
  if (t.fire != FireMode::None && t.fireAmount > 0) {
    float f = clamp01(fireAt(fx, fy, ux, uy, r) * t.fireAmount * clamp01(s_.glow));
    if (f > 0) {
      Rgb fc;
      if (f < 0.33f) {
        float k = f / 0.33f;
        fc = {t.fireLow.r * k, t.fireLow.g * k, t.fireLow.b * k};
      } else if (f < 0.66f) {
        float k = (f - 0.33f) / 0.33f;
        fc = {lerp(t.fireLow.r, t.fireMid.r, k), lerp(t.fireLow.g, t.fireMid.g, k), lerp(t.fireLow.b, t.fireMid.b, k)};
      } else {
        float k = (f - 0.66f) / 0.34f;
        fc = {lerp(t.fireMid.r, t.fireHigh.r, k), lerp(t.fireMid.g, t.fireHigh.g, k),
              lerp(t.fireMid.b, t.fireHigh.b, k)};
      }
      col.r += fc.r;
      col.g += fc.g;
      col.b += fc.b;
    }
  }

  // --- Pupil ---
  float pupilA = 0;  // coverage of the pupil shape; inverted themes apply it after the glow
  float pupilEdge = 1e9f;
  if (t.pupilShape != PupilShape::None &&
      (t.pupilInvert || r < pupilR_ * 1.8f + t.irisRadius * t.slitHeight + 2)) {
    float edge = pupilEdgeAt(dx, dy, r);
    pupilEdge = edge;
    pupilA = 1.0f - smoothstep(-1.0f, 1.0f, edge);
    if (!t.pupilInvert && pupilA > 0) {
      col.r = lerp(col.r, pupilColor_.r, pupilA);
      col.g = lerp(col.g, pupilColor_.g, pupilA);
      col.b = lerp(col.b, pupilColor_.b, pupilA);
    }
  }

  // --- Glow ---
  if (glowAmount_ > 0) {
    float g = glowAmount_ * glowK_ * expNeg(r * r * glowInvR2_);
    col.r += glowColor_.r * g;
    col.g += glowColor_.g * g;
    col.b += glowColor_.b * g;
  }

  // --- Specular (light from upper-left for both eyes) ---
  if (t.specular > 0) {
    float hx = dx + t.irisRadius * 0.32f, hy = dy + t.irisRadius * 0.38f;
    float hr = t.irisRadius * 0.13f;
    float h = expNeg(fdiv(hx * hx + hy * hy, hr * hr));
    float h2x = dx - t.irisRadius * 0.3f, h2y = dy - t.irisRadius * 0.28f;
    float h2r = t.irisRadius * 0.06f;
    h += 0.5f * expNeg(fdiv(h2x * h2x + h2y * h2y, h2r * h2r));
    h *= t.specular * 0.85f;
    col.r += h;
    col.g += h;
    col.b += h;
  }
  if (t.sparkle > 0 && r < t.irisRadius) {
    // Twinkling four-point stars: a diamond core with thin rays along the axes.
    float s1x = fabsf(dx - t.irisRadius * 0.42f), s1y = fabsf(dy + t.irisRadius * 0.05f);
    float s2x = fabsf(dx + t.irisRadius * 0.15f), s2y = fabsf(dy - t.irisRadius * 0.5f);
    float w = t.irisRadius * 0.045f;
    float m1 = s1x < s1y ? s1x : s1y, m2 = s2x < s2y ? s2x : s2y;
    const float iw = fastRecip(w);
    float a1 = expNeg((s1x + s1y) * iw * (1.0f / 3.2f) + m1 * iw * (1.0f / 0.35f));
    float a2 = expNeg((s2x + s2y) * iw * (1.0f / 2.2f) + m2 * iw * (1.0f / 0.35f));
    float h = t.sparkle * (a1 * sparkleK_[0] + 0.8f * a2 * sparkleK_[1]);
    col.r += h;
    col.g += h;
    col.b += h;
  }
  if (haze_ > 0) {
    // Cataract: a milky veil over the iris and pupil.
    float v = haze_ * (1.0f - smoothstep(t.irisRadius * 0.7f, t.irisRadius + 4.0f, r));
    col.r = lerp(col.r, 0.80f, v);
    col.g = lerp(col.g, 0.82f, v);
    col.b = lerp(col.b, 0.78f, v);
  }

  // Cut-out themes: everything outside the shape goes dark, with a little light bleeding past the edge.
  if (t.pupilInvert && t.pupilShape != PupilShape::None) {
    float out = 1.0f - pupilA;
    float bleed = pupilEdge > 0 ? 0.3f * glowK_ * expNeg(pupilEdge / 7.0f) : 0;
    col.r = lerp(col.r, pupilColor_.r + glowColor_.r * bleed, out);
    col.g = lerp(col.g, pupilColor_.g + glowColor_.g * bleed, out);
    col.b = lerp(col.b, pupilColor_.b + glowColor_.b * bleed, out);
  }

  // Spherical shading toward the rim, lid shadows.
  float k = rim * shadow;
  col.r *= k;
  col.g *= k;
  col.b *= k;

  if (t.scanlines > 0) {
    if (py % 3 == 0) {
      float d = 1.0f - 0.55f * t.scanlines;
      col.r *= d;
      col.g *= d;
      col.b *= d;
    }
    float bar = fmodf(s_.time * 70.0f, 340.0f) - 50.0f;
    float d = (fy - bar) / 18.0f;
    float b = 0.12f * t.scanlines * expNeg(d * d) * clamp01(s_.glow);
    col.r += t.glowColor.r * b;
    col.g += t.glowColor.g * b;
    col.b += t.glowColor.b * b;
  }

  if (lidA > 0) {
    col.r = lerp(col.r, lid.r, lidA);
    col.g = lerp(col.g, lid.g, lidA);
    col.b = lerp(col.b, lid.b, lidA);
  }
  return col;
}

// Fast path (half-res themes without per-frame patterns): the static eyeball comes from the baked
// base image slid by the gaze; only lids, pupil, fire, glow, highlights, haze, rim and scanlines
// are computed per sample, most of them without a square root.
HOT Renderer::Col Renderer::shadeFast(int px, int py, float fx, float fy) const {
  const ThemeSpec& t = *t_;
  const float sdx = fx - kCentre, sdy = fy - kCentre;
  const float rs2 = sdx * sdx + sdy * sdy;
  const float rim = rs2 < 70.0f * 70.0f ? 1.0f : 1.0f - 0.45f * smoothstep(70.0f, 121.0f, fastSqrt(rs2));

  // --- Eyelids ---
  float lidA = 0, lidEdgeY = 0, shadow = 1;
  if (t.lids) {
    const float yTop = lidTop_[px], yBot = lidBot_[px];
    float topA = 1.0f - smoothstep(yTop - 0.7f, yTop + 0.7f, fy);
    float botA = smoothstep(yBot - 0.7f, yBot + 0.7f, fy);
    lidA = topA > botA ? topA : botA;
    lidEdgeY = topA > botA ? yTop : yBot;
    if (fy > yTop) shadow *= 1.0f - 0.55f * expNeg((fy - yTop) * (1.0f / 9.0f));
    if (fy < yBot) shadow *= 1.0f - 0.35f * expNeg((yBot - fy) * (1.0f / 7.0f));
  }
  Col lid = {0, 0, 0};
  if (lidA > 0) {
    float dist = fabsf(fy - lidEdgeY);
    float k = 0.7f + 0.3f * smoothstep(0.0f, 45.0f, dist);
    float e = smoothstep(1.5f, 5.0f, dist);
    lid.r = lerp(t.lidEdge.r, t.lidColor.r * k, e) * rim;
    lid.g = lerp(t.lidEdge.g, t.lidColor.g * k, e) * rim;
    lid.b = lerp(t.lidEdge.b, t.lidColor.b * k, e) * rim;
    if (lidA >= 1.0f) return lid;
  }

  // --- Baked eyeball ---
  const float dx = fx - cx_, dy = fy - cy_;
  const float r2 = dx * dx + dy * dy;
  const int bi = (int)(dx * 0.5f + baseHalf_), bj = (int)(dy * 0.5f + baseHalf_);
  Col col;
  const Texel* tex = nullptr;
  float irisM = 0;
  constexpr float k255 = 1.0f / 255.0f;
  if ((unsigned)bi < (unsigned)baseN_ && (unsigned)bj < (unsigned)baseN_) {
    tex = &base_[bj * baseN_ + bi];
    col = {tex->r * k255, tex->g * k255, tex->b * k255};
    irisM = tex->iris * k255;
    if (irisM > 0) {
      if (t.spiral > 0) {
        // Hypnotic bands: angle and radius come from the texel, sine from a table.
        float a = tex->ang * (kTwoPi / 65535.0f) - kPi, rr = tex->rad * (1.0f / 64.0f);
        float band = 0.5f + 0.5f * fastSin(a * 2.0f + rr * 0.16f - s_.time * t.spiralSpeed);
        const float sb = smoothstep(0.3f, 0.7f, band), w = t.spiral * irisM;
        col.r = lerp(col.r, lerp(irisInner_.r, irisOuter_.r, sb), w);
        col.g = lerp(col.g, lerp(irisInner_.g, irisOuter_.g, sb), w);
        col.b = lerp(col.b, lerp(irisInner_.b, irisOuter_.b, sb), w);
      }
      if (hueOn_) {
        Col h = {hue_[0] * col.r + hue_[1] * col.g + hue_[2] * col.b,
                 hue_[3] * col.r + hue_[4] * col.g + hue_[5] * col.b,
                 hue_[6] * col.r + hue_[7] * col.g + hue_[8] * col.b};
        col.r = lerp(col.r, h.r, irisM);
        col.g = lerp(col.g, h.g, irisM);
        col.b = lerp(col.b, h.b, irisM);
      }
      if (emissive_ != 1.0f) {
        float k = 1.0f + (emissive_ - 1.0f) * irisM;
        col.r *= k;
        col.g *= k;
        col.b *= k;
      }
    }
  } else {
    col = {t.scleraEdge.r, t.scleraEdge.g, t.scleraEdge.b};
  }

  float r = -1;  // computed lazily: only some layers need the true distance
  auto dist = [&]() {
    if (r < 0) r = fastSqrt(r2) + 1e-4f;
    return r;
  };

  // --- Fire ---
  if (t.fire != FireMode::None && t.fireAmount > 0) {
    float f;
    if (t.fire == FireMode::Radial && tex) {
      f = fireRadialAt(tex->ang * (1.0f / 65535.0f), tex->rad * (1.0f / 64.0f));
    } else if (t.fire != FireMode::Radial) {
      const float gx = fx * (1.0f / kFireGrid), gy = fy * (1.0f / kFireGrid);
      const int x0 = (int)gx, y0 = (int)gy;
      f = fireGridAt(fx, fy, tex ? tex->rad * (1.0f / 64.0f) : dist(), fire_ + y0 * kFireCells + x0, gx - x0, gy - y0);
    } else {
      const float rr = dist(), inv = fastRecip(rr);
      f = fireAt(fx, fy, dx * inv, dy * inv, rr);
    }
    f = clamp01(f * t.fireAmount * clamp01(s_.glow));
    if (f > 0) {
      Rgb fc;
      if (f < 0.33f) {
        float k = f * (1.0f / 0.33f);
        fc = {t.fireLow.r * k, t.fireLow.g * k, t.fireLow.b * k};
      } else if (f < 0.66f) {
        float k = (f - 0.33f) * (1.0f / 0.33f);
        fc = {lerp(t.fireLow.r, t.fireMid.r, k), lerp(t.fireLow.g, t.fireMid.g, k), lerp(t.fireLow.b, t.fireMid.b, k)};
      } else {
        float k = (f - 0.66f) * (1.0f / 0.34f);
        fc = {lerp(t.fireMid.r, t.fireHigh.r, k), lerp(t.fireMid.g, t.fireHigh.g, k),
              lerp(t.fireMid.b, t.fireHigh.b, k)};
      }
      col.r += fc.r;
      col.g += fc.g;
      col.b += fc.b;
    }
  }

  // --- Pupil ---
  float pupilA = 0, pupilEdge = 1e9f;
  if (t.pupilShape != PupilShape::None) {
    const float bound = pupilR_ * 1.8f + t.irisRadius * t.slitHeight + 2;
    if (t.pupilInvert || r2 < bound * bound) {
      pupilEdge = pupilEdgeAt(dx, dy, dist());
      pupilA = 1.0f - smoothstep(-1.0f, 1.0f, pupilEdge);
      if (!t.pupilInvert && pupilA > 0) {
        col.r = lerp(col.r, pupilColor_.r, pupilA);
        col.g = lerp(col.g, pupilColor_.g, pupilA);
        col.b = lerp(col.b, pupilColor_.b, pupilA);
      }
    }
  }

  // --- Glow ---
  if (glowAmount_ > 0) {
    float g = glowAmount_ * glowK_ * expNeg(r2 * glowInvR2_);
    col.r += glowColor_.r * g;
    col.g += glowColor_.g * g;
    col.b += glowColor_.b * g;
  }

  // --- Highlights ---
  if (t.specular > 0) {
    float hx = dx + t.irisRadius * 0.32f, hy = dy + t.irisRadius * 0.38f;
    float h = expNeg((hx * hx + hy * hy) * specInvA_);
    float h2x = dx - t.irisRadius * 0.3f, h2y = dy - t.irisRadius * 0.28f;
    h += 0.5f * expNeg((h2x * h2x + h2y * h2y) * specInvB_);
    h *= t.specular * 0.85f;
    col.r += h;
    col.g += h;
    col.b += h;
  }
  if (t.sparkle > 0 && r2 < t.irisRadius * t.irisRadius) {
    float s1x = fabsf(dx - t.irisRadius * 0.42f), s1y = fabsf(dy + t.irisRadius * 0.05f);
    float s2x = fabsf(dx + t.irisRadius * 0.15f), s2y = fabsf(dy - t.irisRadius * 0.5f);
    float w = t.irisRadius * 0.045f;
    float m1 = s1x < s1y ? s1x : s1y, m2 = s2x < s2y ? s2x : s2y;
    const float iw = fastRecip(w);
    float a1 = expNeg((s1x + s1y) * iw * (1.0f / 3.2f) + m1 * iw * (1.0f / 0.35f));
    float a2 = expNeg((s2x + s2y) * iw * (1.0f / 2.2f) + m2 * iw * (1.0f / 0.35f));
    float h = t.sparkle * (a1 * sparkleK_[0] + 0.8f * a2 * sparkleK_[1]);
    col.r += h;
    col.g += h;
    col.b += h;
  }
  if (haze_ > 0 && r2 < (t.irisRadius + 4.0f) * (t.irisRadius + 4.0f)) {
    float v = haze_ * (1.0f - smoothstep(t.irisRadius * 0.7f, t.irisRadius + 4.0f, dist()));
    col.r = lerp(col.r, 0.80f, v);
    col.g = lerp(col.g, 0.82f, v);
    col.b = lerp(col.b, 0.78f, v);
  }

  // Cut-out themes: everything outside the shape goes dark, with a little light bleeding past the edge.
  if (t.pupilInvert && t.pupilShape != PupilShape::None) {
    float out = 1.0f - pupilA;
    float bleed = pupilEdge > 0 ? 0.3f * glowK_ * expNeg(pupilEdge * (1.0f / 7.0f)) : 0;
    col.r = lerp(col.r, pupilColor_.r + glowColor_.r * bleed, out);
    col.g = lerp(col.g, pupilColor_.g + glowColor_.g * bleed, out);
    col.b = lerp(col.b, pupilColor_.b + glowColor_.b * bleed, out);
  }

  float k = rim * shadow;
  col.r *= k;
  col.g *= k;
  col.b *= k;

  if (t.scanlines > 0) {
    if (py % 3 == 0) {
      float d = 1.0f - 0.55f * t.scanlines;
      col.r *= d;
      col.g *= d;
      col.b *= d;
    }
    float bar = fmodf(s_.time * 70.0f, 340.0f) - 50.0f;
    float d = (fy - bar) * (1.0f / 18.0f);
    float b = 0.12f * t.scanlines * expNeg(d * d) * clamp01(s_.glow);
    col.r += t.glowColor.r * b;
    col.g += t.glowColor.g * b;
    col.b += t.glowColor.b * b;
  }

  if (lidA > 0) {
    col.r = lerp(col.r, lid.r, lidA);
    col.g = lerp(col.g, lid.g, lidA);
    col.b = lerp(col.b, lid.b, lidA);
  }
  return col;
}

namespace {
inline uint16_t quantize(float cr, float cg, float cb, float d, bool byteSwap) {
  int r = (int)(cr * 31.0f + 0.5f + d);
  int g = (int)(cg * 63.0f + 0.5f + d);
  int b = (int)(cb * 31.0f + 0.5f + d);
  r = r < 0 ? 0 : (r > 31 ? 31 : r);
  g = g < 0 ? 0 : (g > 63 ? 63 : g);
  b = b < 0 ? 0 : (b > 31 ? 31 : b);
  uint16_t px = (uint16_t)((r << 11) | (g << 5) | b);
  return byteSwap ? (uint16_t)((px >> 8) | (px << 8)) : px;
}
}  // namespace

HOT void Renderer::renderRowsHalf(int y0, int y1, uint16_t* out, bool byteSwap) const {
  for (int y = y0; y < y1; y += 2) {
    uint16_t* row0 = out + (y - y0) * kSize;
    uint16_t* row1 = row0 + kSize;
    const float fy = y + 1.0f;  // centre of the 2x2 block
    const float sdy = fy - kCentre;
    float half2 = (kScreenR + 2.5f) * (kScreenR + 2.5f) - sdy * sdy;
    int xs = kSize, xe = kSize;
    if (half2 > 0) {
      float h = fastSqrt(half2);
      xs = ((int)(kCentre - h)) & ~1;
      xe = (int)(kCentre + h) + 2;
      if (xs < 0) xs = 0;
      if (xe > kSize) xe = kSize;
    }
    memset(row0, 0, kSize * 2);
    memset(row1, 0, kSize * 2);
    for (int x = xs; x + 1 < xe; x += 2) {
      Col c = fast_ ? shadeFast(x, y, x + 1.0f, fy) : shade(x, y, x + 1.0f, fy);
      // Convert once to 8-bit, then dither the four output pixels with integer math only.
      const int r8 = (int)(clamp01(c.r) * 255.0f + 0.5f), g8 = (int)(clamp01(c.g) * 255.0f + 0.5f),
                b8 = (int)(clamp01(c.b) * 255.0f + 0.5f);
      const int r31 = r8 * 31 * 16, g63 = g8 * 63 * 16, b31 = b8 * 31 * 16;
      const uint8_t* b0 = kBayer[y & 3];
      const uint8_t* b1 = kBayer[(y + 1) & 3];
      auto px565 = [&](int bayer) {
        const int d = bayer * 255 + 128;  // Bayer offset in the same 1/(255*16) units
        int r = (r31 + d) / 4080, g = (g63 + d) / 4080, b = (b31 + d) / 4080;
        uint16_t p = (uint16_t)(((r > 31 ? 31 : r) << 11) | ((g > 63 ? 63 : g) << 5) | (b > 31 ? 31 : b));
        return byteSwap ? (uint16_t)((p >> 8) | (p << 8)) : p;
      };
      row0[x] = px565(b0[x & 3]);
      row0[x + 1] = px565(b0[(x + 1) & 3]);
      row1[x] = px565(b1[x & 3]);
      row1[x + 1] = px565(b1[(x + 1) & 3]);
    }
  }
}

void Renderer::renderRows(int y0, int y1, uint16_t* out, bool byteSwap) const {
  if (halfRes_) return renderRowsHalf(y0, y1, out, byteSwap);
  for (int y = y0; y < y1; ++y) {
    uint16_t* row = out + (y - y0) * kSize;
    const float sdy = y + 0.5f - kCentre;
    // Only shade the span inside the round panel (+1.5 px margin); the rest is black.
    float half2 = (kScreenR + 1.5f) * (kScreenR + 1.5f) - sdy * sdy;
    int xs = kSize, xe = kSize;
    if (half2 > 0) {
      float h = fastSqrt(half2);
      xs = (int)(kCentre - h);
      xe = (int)(kCentre + h) + 1;
      if (xs < 0) xs = 0;
      if (xe > kSize) xe = kSize;
    }
    if (xs >= xe) {
      memset(row, 0, kSize * 2);
      continue;
    }
    memset(row, 0, xs * 2);
    memset(row + xe, 0, (kSize - xe) * 2);
    for (int x = xs; x < xe; ++x) {
      Col c = shade(x, y, x + 0.5f, y + 0.5f);
      float d = (kBayer[y & 3][x & 3] + 0.5f) / 16.0f - 0.5f;
      int r = (int)(clamp01(c.r) * 31.0f + 0.5f + d);
      int g = (int)(clamp01(c.g) * 63.0f + 0.5f + d);
      int b = (int)(clamp01(c.b) * 31.0f + 0.5f + d);
      r = r < 0 ? 0 : (r > 31 ? 31 : r);
      g = g < 0 ? 0 : (g > 63 ? 63 : g);
      b = b < 0 ? 0 : (b > 31 ? 31 : b);
      uint16_t px = (uint16_t)((r << 11) | (g << 5) | b);
      if (byteSwap) px = (uint16_t)((px >> 8) | (px << 8));
      row[x] = px;
    }
  }
}

}  // namespace eyes
