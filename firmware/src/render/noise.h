// Cheap hash-based value noise, good enough for fibres, veins and flames.
#pragma once
#include <math.h>
#include <stdint.h>

namespace eyes {

inline uint32_t hash3(int x, int y, int z) {
  uint32_t h = (uint32_t)x * 374761393u + (uint32_t)y * 668265263u + (uint32_t)z * 2246822519u;
  h = (h ^ (h >> 13)) * 1274126177u;
  return h ^ (h >> 16);
}

inline float lattice(int x, int y, int z) { return (hash3(x, y, z) & 0xffff) * (1.0f / 65535.0f); }

inline float smooth01(float t) { return t * t * (3.0f - 2.0f * t); }

// Value noise in [0,1].
inline float vnoise(float x, float y, float z) {
  float fx = floorf(x), fy = floorf(y), fz = floorf(z);
  int ix = (int)fx, iy = (int)fy, iz = (int)fz;
  float tx = smooth01(x - fx), ty = smooth01(y - fy), tz = smooth01(z - fz);
  float c000 = lattice(ix, iy, iz), c100 = lattice(ix + 1, iy, iz);
  float c010 = lattice(ix, iy + 1, iz), c110 = lattice(ix + 1, iy + 1, iz);
  float c001 = lattice(ix, iy, iz + 1), c101 = lattice(ix + 1, iy, iz + 1);
  float c011 = lattice(ix, iy + 1, iz + 1), c111 = lattice(ix + 1, iy + 1, iz + 1);
  float x00 = c000 + (c100 - c000) * tx, x10 = c010 + (c110 - c010) * tx;
  float x01 = c001 + (c101 - c001) * tx, x11 = c011 + (c111 - c011) * tx;
  float y0 = x00 + (x10 - x00) * ty, y1 = x01 + (x11 - x01) * ty;
  return y0 + (y1 - y0) * tz;
}

// Three-octave fractal noise in [0,1].
inline float fbm3(float x, float y, float z) {
  return (vnoise(x, y, z) * 0.5714f + vnoise(x * 2.03f, y * 2.03f, z * 2.03f) * 0.2857f +
          vnoise(x * 4.1f, y * 4.1f, z * 4.1f) * 0.1429f);
}

// Fixed-point twin of fbm3() for per-frame fields: the ESP32-S3 FPU takes ~7 cycles per dependent
// float op, while integer multiplies are single-cycle. Same lattice/hash, so the pattern matches.
inline int32_t vnoiseFix(int32_t x, int32_t y, int32_t z) {  // 16.16 inputs, 0..65535 out
  const int ix = x >> 16, iy = y >> 16, iz = z >> 16;
  // 12-bit smoothstep weights keep every product inside 32 bits.
  auto smooth12 = [](int32_t f16) {
    int32_t f = f16 >> 4;  // 0..4095
    return (f * f >> 12) * (3 * 4096 - 2 * f) >> 12;
  };
  const int32_t tx = smooth12(x & 0xffff), ty = smooth12(y & 0xffff), tz = smooth12(z & 0xffff);
  auto L = [](int a, int b, int c) { return (int32_t)(hash3(a, b, c) & 0xffff); };
  auto mix = [](int32_t a, int32_t b, int32_t t) { return a + (((b - a) * t) >> 12); };
  int32_t x00 = mix(L(ix, iy, iz), L(ix + 1, iy, iz), tx);
  int32_t x10 = mix(L(ix, iy + 1, iz), L(ix + 1, iy + 1, iz), tx);
  int32_t x01 = mix(L(ix, iy, iz + 1), L(ix + 1, iy, iz + 1), tx);
  int32_t x11 = mix(L(ix, iy + 1, iz + 1), L(ix + 1, iy + 1, iz + 1), tx);
  return mix(mix(x00, x10, ty), mix(x01, x11, ty), tz);
}

inline float fbm3Fix(float x, float y, float z) {
  // Wrap into [0, 4096) so time-scrolled coordinates never overflow 16.16 (or the x4.1 octave).
  // The lattice isn't periodic, so this is a one-frame jump every few thousand units -- invisible in fire.
  auto wrap = [](float v) { return v - 4096.0f * floorf(v * (1.0f / 4096.0f)); };
  const int32_t X = (int32_t)(wrap(x) * 65536.0f), Y = (int32_t)(wrap(y) * 65536.0f),
                Z = (int32_t)(wrap(z) * 65536.0f);
  // Octave scales 2.03 and 4.1 as 16.16-friendly integer ratios (x * 2079/1024, x * 4198/1024).
  auto sc = [](int32_t v, int32_t k) { return (int32_t)(((int64_t)v * k) >> 10); };
  // Weights 0.5714/0.2857/0.1429 * 65536; the sum needs the full unsigned 32-bit range.
  uint32_t n = (uint32_t)vnoiseFix(X, Y, Z) * 37450u + (uint32_t)vnoiseFix(sc(X, 2079), sc(Y, 2079), sc(Z, 2079)) * 18725u +
               (uint32_t)vnoiseFix(sc(X, 4198), sc(Y, 4198), sc(Z, 4198)) * 9365u;
  return (float)(n >> 16) * (1.0f / 65535.0f);
}

}  // namespace eyes
