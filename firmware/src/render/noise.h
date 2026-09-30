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

}  // namespace eyes
