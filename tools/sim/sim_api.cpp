// C API over the firmware renderer + behaviour engine, for desktop simulators (ctypes).
#include <stdint.h>
#include <string.h>

#include "controller.h"
#include "renderer.h"
#include "theme.h"

using namespace eyes;

namespace {
struct Sim {
  EyeController ctl;
  ThemeCache cache;
  Renderer ren[2];
  uint16_t px[kSize * kSize];
  explicit Sim(uint32_t seed) : ctl(seed) {}
};
}  // namespace

extern "C" {

int sim_theme_count() { return themeCount(); }
const char* sim_theme_id(int i) { return themeAt(i) ? themeAt(i)->id : nullptr; }
const char* sim_theme_name(int i) { return themeAt(i) ? themeAt(i)->name : nullptr; }
const char* sim_theme_category(int i) {
  return themeAt(i) && themeAt(i)->category ? themeAt(i)->category : "other";
}
int sim_mood_count() { return kMoodCount; }
const char* sim_mood_name(int i) { return i >= 0 && i < kMoodCount ? moodName((Mood)i) : nullptr; }

void* sim_new(uint32_t seed, const char* theme) {
  Sim* s = new Sim(seed);
  const ThemeSpec* t = themeById(theme);
  if (!t) t = themeAt(0);
  s->cache.build(t);
  s->ctl.setTheme(t);
  for (auto& r : s->ren) r.setCache(&s->cache);
  return s;
}

void sim_free(void* p) { delete (Sim*)p; }

int sim_set_theme(void* p, const char* id) {
  Sim* s = (Sim*)p;
  const ThemeSpec* t = themeById(id);
  if (!t) return -1;
  if (t != s->cache.theme()) s->cache.build(t);
  s->ctl.setTheme(t);
  return 0;
}

int sim_set_mood(void* p, const char* name) {
  Mood m;
  if (!moodFromName(name, &m)) return -1;
  ((Sim*)p)->ctl.setMood(m);
  return 0;
}

void sim_set_autonomous(void* p, int on) { ((Sim*)p)->ctl.setAutonomous(on != 0); }
void sim_set_pupil(void* p, float v) { ((Sim*)p)->ctl.setPupilOverride(v); }
void sim_look(void* p, float x, float y, float hold) { ((Sim*)p)->ctl.look(x, y, hold); }
void sim_release(void* p) { ((Sim*)p)->ctl.release(); }
void sim_blink(void* p) { ((Sim*)p)->ctl.blink(); }
void sim_wink(void* p, int eye) { ((Sim*)p)->ctl.wink(eye); }
void sim_startle(void* p) { ((Sim*)p)->ctl.startle(); }
void sim_roll(void* p) { ((Sim*)p)->ctl.roll(); }
void sim_update(void* p, float dt) { ((Sim*)p)->ctl.update(dt); }
float sim_target_x(void* p) { return ((Sim*)p)->ctl.targetX(); }
float sim_target_y(void* p) { return ((Sim*)p)->ctl.targetY(); }

// Renders one eye (0 = left, 1 = right) as 240x240 RGB888 into out.
void sim_render_eye(void* p, int eye, uint8_t* out) {
  Sim* s = (Sim*)p;
  s->ren[eye].begin(s->ctl.eye(eye));
  s->ren[eye].renderRows(0, kSize, s->px, false);
  for (int i = 0; i < kSize * kSize; ++i) {
    uint16_t v = s->px[i];
    out[i * 3] = (uint8_t)(((v >> 11) & 31) * 255 / 31);
    out[i * 3 + 1] = (uint8_t)(((v >> 5) & 63) * 255 / 63);
    out[i * 3 + 2] = (uint8_t)((v & 31) * 255 / 31);
  }
}

}  // extern "C"
