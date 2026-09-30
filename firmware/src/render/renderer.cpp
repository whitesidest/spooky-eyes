#include "renderer.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "noise.h"

#if defined(ESP_PLATFORM)
#include <esp_heap_caps.h>
#pragma GCC optimize("O3")
#endif

namespace eyes {
namespace {

constexpr float kCentre = kSize / 2.0f;
constexpr float kScreenR = kSize / 2.0f;
constexpr float kPi = 3.14159265f;
constexpr float kTwoPi = 6.2831853f;

void* bigAlloc(size_t bytes) {
#if defined(ESP_PLATFORM)
  void* p = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
  return p ? p : malloc(bytes);
#else
  return malloc(bytes);
#endif
}

inline float clamp01(float v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
inline float lerp(float a, float b, float t) { return a + (b - a) * t; }
inline float smoothstep(float e0, float e1, float x) { return smooth01(clamp01((x - e0) / (e1 - e0))); }

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
  float a = mx > 0 ? mn / mx : 0;
  float s = a * a;
  float r = ((-0.0464964749f * s + 0.15931422f) * s - 0.327622764f) * s * a + a;
  if (ay > ax) r = 1.57079637f - r;
  if (x < 0) r = kPi - r;
  if (y < 0) r = -r;
  return r;
}

const uint8_t kBayer[4][4] = {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};

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
  fire_ = (float*)bigAlloc(sizeof(float) * (cells > polar ? cells : polar));
}

Renderer::~Renderer() { free(fire_); }

void Renderer::begin(const EyeState& s) {
  s_ = s;
  t_ = cache_->theme();
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
  float swirl = fmodf(t.irisSwirl * s.time, kTwoPi);
  swirlOffset_ = (int)(swirl * ThemeCache::kIrisAngles / kTwoPi);

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
  if (t.fire != FireMode::None && t.fireAmount > 0) buildFireField();
}

void Renderer::buildFireField() {
  const ThemeSpec& t = *t_;
  const float z = s_.mirror ? 17.0f : 0.0f;  // different flames per eye
  if (t.fire == FireMode::Radial) {
    fireR0_ = t.fireInner - 12.0f;
    fireRStep_ = (t.fireOuter + 10.0f - fireR0_) / (kFireRadii - 1);
    const float fs = t.fireScale;
    for (int ai = 0; ai < kFireAngles; ++ai) {
      float a = ai * kTwoPi / kFireAngles - kPi;
      float ux = cosf(a), uy = sinf(a);
      for (int ri = 0; ri < kFireRadii; ++ri) {
        float r = fireR0_ + ri * fireRStep_;
        fire_[ri * kFireAngles + ai] =
            fbm3(ux * fs * 3.0f + 11.0f + z, uy * fs * 3.0f, r * fs * 0.09f - s_.time * t.fireSpeed);
      }
    }
  } else {
    for (int gy = 0; gy < kFireCells; ++gy) {
      for (int gx = 0; gx < kFireCells; ++gx) {
        float fx = gx * kFireGrid, fy = gy * kFireGrid;
        fire_[gy * kFireCells + gx] =
            fbm3(fx * t.fireScale + z, fy * t.fireScale + s_.time * t.fireSpeed, s_.time * 0.35f);
      }
    }
  }
}

float Renderer::fireAt(float fx, float fy, float ux, float uy, float r) const {
  const ThemeSpec& t = *t_;
  float n;
  if (t.fire == FireMode::Radial) {
    float band = smoothstep(t.fireInner - 12.0f, t.fireInner + 6.0f, r) *
                 (1.0f - smoothstep(t.fireOuter - 25.0f, t.fireOuter + 10.0f, r));
    if (band <= 0) return 0;
    float af = (fastAtan2(uy, ux) + kPi) * (kFireAngles / kTwoPi);
    float rf = (r - fireR0_) / fireRStep_;
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
  float dx = fx - cx_, dy = fy - cy_;
  float up = dy < 0 ? dy * 0.45f : dy;
  float rr = sqrtf(dx * dx + up * up);
  float base = smoothstep(t.fireInner - 8.0f, t.fireInner + 4.0f, r) * (1.0f - smoothstep(t.fireInner, t.fireOuter, rr));
  if (base <= 0) return 0;
  float gx = fx / kFireGrid, gy = fy / kFireGrid;
  int x0 = (int)gx, y0 = (int)gy;
  float tx = gx - x0, ty = gy - y0;
  const float* p = fire_ + y0 * kFireCells + x0;
  n = lerp(lerp(p[0], p[1], tx), lerp(p[kFireCells], p[kFireCells + 1], tx), ty);
  return clamp01(base * (n * 2.2f - 0.45f));
}

Renderer::Col Renderer::shade(int px, int py) const {
  const ThemeSpec& t = *t_;
  const float fx = px + 0.5f, fy = py + 0.5f;
  const float sdx = fx - kCentre, sdy = fy - kCentre;
  const float rScreen = sqrtf(sdx * sdx + sdy * sdy);
  const float rim = 1.0f - 0.45f * smoothstep(70.0f, 121.0f, rScreen);

  // --- Eyelids first: fully covered pixels skip the eye entirely ---
  float lidA = 0, lidEdgeY = 0, shadow = 1;
  if (t.lids) {
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

  // Iris-relative coordinates (foreshortened).
  const float dx = (fx - cx_) * squashX_, dy = (fy - cy_) * squashY_;
  const float r = sqrtf(dx * dx + dy * dy) + 1e-4f;
  const float inv = 1.0f / r;
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

  // --- Iris ---
  const float irisA = 1.0f - smoothstep(t.irisRadius - 1.0f, t.irisRadius + 1.0f, r);
  if (irisA > 0) {
    float span = t.irisRadius - pupilR_;
    float tr = clamp01((r - pupilR_) / (span > 1 ? span : 1));
    Col iris = {lerp(t.irisInner.r, t.irisOuter.r, tr), lerp(t.irisInner.g, t.irisOuter.g, tr),
                lerp(t.irisInner.b, t.irisOuter.b, tr)};
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
    float lim = smoothstep(t.irisRadius - t.limbusWidth, t.irisRadius, r) * 0.9f;
    col.r = lerp(col.r, lerp(iris.r, t.limbus.r, lim) * emissive_, irisA);
    col.g = lerp(col.g, lerp(iris.g, t.limbus.g, lim) * emissive_, irisA);
    col.b = lerp(col.b, lerp(iris.b, t.limbus.b, lim) * emissive_, irisA);
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
  if (t.pupilShape != PupilShape::None && r < pupilR_ + t.irisRadius * t.slitHeight + 2) {
    float edge;  // signed distance-ish, <0 inside
    if (t.pupilShape == PupilShape::Round) {
      edge = r - pupilR_;
    } else {
      float q = dy / (t.slitHeight * t.irisRadius);
      float halfW = pupilR_ * (1.0f - q * q);
      edge = fabsf(dx) - (halfW > 0 ? halfW : -1.0f);
    }
    float a = 1.0f - smoothstep(-1.0f, 1.0f, edge);
    if (a > 0) {
      col.r = lerp(col.r, t.pupilColor.r, a);
      col.g = lerp(col.g, t.pupilColor.g, a);
      col.b = lerp(col.b, t.pupilColor.b, a);
    }
  }

  // --- Glow ---
  if (t.glowAmount > 0) {
    float g = t.glowAmount * glowK_ * expNeg((r * r) / (t.glowRadius * t.glowRadius));
    col.r += t.glowColor.r * g;
    col.g += t.glowColor.g * g;
    col.b += t.glowColor.b * g;
  }

  // --- Specular (light from upper-left for both eyes) ---
  if (t.specular > 0) {
    float hx = fx - (cx_ - t.irisRadius * 0.32f), hy = fy - (cy_ - t.irisRadius * 0.38f);
    float hr = t.irisRadius * 0.13f;
    float h = expNeg((hx * hx + hy * hy) / (hr * hr));
    float h2x = fx - (cx_ + t.irisRadius * 0.3f), h2y = fy - (cy_ + t.irisRadius * 0.28f);
    float h2r = t.irisRadius * 0.06f;
    h += 0.5f * expNeg((h2x * h2x + h2y * h2y) / (h2r * h2r));
    h *= t.specular * 0.85f;
    col.r += h;
    col.g += h;
    col.b += h;
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

void Renderer::renderRows(int y0, int y1, uint16_t* out, bool byteSwap) const {
  for (int y = y0; y < y1; ++y) {
    uint16_t* row = out + (y - y0) * kSize;
    const float sdy = y + 0.5f - kCentre;
    // Only shade the span inside the round panel (+1.5 px margin); the rest is black.
    float half2 = (kScreenR + 1.5f) * (kScreenR + 1.5f) - sdy * sdy;
    int xs = kSize, xe = kSize;
    if (half2 > 0) {
      float h = sqrtf(half2);
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
      Col c = shade(x, y);
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
