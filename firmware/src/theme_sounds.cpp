#include "theme_sounds.h"

#include <string.h>

namespace theme_sounds {
namespace {

struct Pair {
  const char* theme;
  const char* sound;
};

// Themes not listed here are silent by default.
const Pair kDefaults[] = {
    {"fire", "growl"},          {"sauron", "whisper"},     {"demon", "growl"},
    {"werewolf", "growl"},      {"dragon", "growl"},       {"zombie", "growl"},
    {"blood_zombie", "growl"},  {"vampire", "whisper"},    {"ghost", "whisper"},
    {"snake", "whisper"},       {"chucky", "whisper"},     {"jack_o_lantern", "creak"},
    {"spider", "creak"},        {"dead", "creak"},         {"saw", "creak"},
    {"terminator", "zap"},      {"robot", "zap"},          {"alien", "zap"},
    {"fireworks", "zap"},       {"hypnotic", "chime"},     {"rainbow", "chime"},
    {"frost", "chime"},         {"valentine", "chime"},    {"easter", "chime"},
    {"st_patricks", "chime"},   {"anime", "chime"},        {"witch", "whisper"},
};

}  // namespace

const char* defaultFor(const char* themeId) {
  for (const auto& p : kDefaults)
    if (!strcmp(p.theme, themeId)) return p.sound;
  return nullptr;
}

}  // namespace theme_sounds
