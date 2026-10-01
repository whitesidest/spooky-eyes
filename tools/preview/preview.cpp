// Renders themes to PPM frames on the desktop: preview <theme|all> <outdir> [frames] [fps] [mood]
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <chrono>
#include <string>
#include <vector>

#include "controller.h"
#include "renderer.h"
#include "theme.h"

using namespace eyes;

static void writePair(const std::string& path, const std::vector<uint16_t>& l, const std::vector<uint16_t>& r) {
  const int gap = 20, w = kSize * 2 + gap, h = kSize;
  FILE* f = fopen(path.c_str(), "wb");
  fprintf(f, "P6\n%d %d\n255\n", w, h);
  std::vector<uint8_t> row(w * 3);
  for (int y = 0; y < h; ++y) {
    for (int x = 0; x < w; ++x) {
      uint16_t p = 0;
      if (x < kSize) p = l[y * kSize + x];
      else if (x >= kSize + gap) p = r[y * kSize + x - kSize - gap];
      row[x * 3] = ((p >> 11) & 31) * 255 / 31;
      row[x * 3 + 1] = ((p >> 5) & 63) * 255 / 63;
      row[x * 3 + 2] = (p & 31) * 255 / 31;
    }
    fwrite(row.data(), 1, row.size(), f);
  }
  fclose(f);
}

int main(int argc, char** argv) {
  if (argc < 3) {
    fprintf(stderr, "usage: preview <theme|all> <outdir> [frames=1] [fps=20] [mood=neutral] [action]\n");
    return 1;
  }
  const std::string which = argv[1], out = argv[2];
  const int frames = argc > 3 ? atoi(argv[3]) : 1;
  const float fps = argc > 4 ? atof(argv[4]) : 20;
  Mood mood = Mood::Neutral;
  if (argc > 5 && !moodFromName(argv[5], &mood)) {
    fprintf(stderr, "unknown mood %s\n", argv[5]);
    return 1;
  }
  // Optional action fired one fifth of the way in: blink, wink_left, wink_right, startle, roll.
  const std::string action = argc > 6 ? argv[6] : "";
  const int actionFrame = frames / 5;
  std::vector<uint16_t> left(kSize * kSize), right(kSize * kSize);
  for (int ti = 0; ti < themeCount(); ++ti) {
    const ThemeSpec* theme = themeAt(ti);
    if (which != "all" && which != theme->id) continue;
    EyeController ctl;
    ctl.setTheme(theme);
    ctl.setMood(mood);
    ThemeCache cache;
    cache.build(theme);
    Renderer ren[2];
    ren[0].setCache(&cache);
    ren[1].setCache(&cache);
    // HALFRES=1 previews the device's half-resolution shading.
    ren[0].setHalfRes(getenv("HALFRES") != nullptr);
    ren[1].setHalfRes(getenv("HALFRES") != nullptr);
    // Settle lids/pupils (and get past the first scheduled blink) before capturing.
    for (int i = 0; i < 64; ++i) ctl.update(0.05f);
    double renderMs = 0;
    for (int f = 0; f < frames; ++f) {
      if (f == actionFrame && !action.empty()) {
        if (action == "blink") ctl.blink();
        else if (action == "wink_left") ctl.wink(0);
        else if (action == "wink_right") ctl.wink(1);
        else if (action == "startle") ctl.startle();
        else if (action == "roll") ctl.roll();
        else if (action == "look") ctl.look(0.8f, -0.3f, 0);
      }
      ctl.update(1.0f / fps);
      auto t0 = std::chrono::steady_clock::now();
      ren[0].begin(ctl.eye(0));
      ren[0].renderRows(0, kSize, left.data(), false);
      ren[1].begin(ctl.eye(1));
      ren[1].renderRows(0, kSize, right.data(), false);
      renderMs += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
      char name[64];
      snprintf(name, sizeof name, "/%s_%04d.ppm", theme->id, f);
      writePair(out + name, left, right);
    }
    printf("%-11s %d frame(s), %.2f ms/frame (both eyes, desktop)\n", theme->id, frames, renderMs / frames);
  }
  return 0;
}
