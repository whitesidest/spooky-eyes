// Default "startle" sound for each theme (overridable per board, per theme, via the API).
#pragma once

namespace theme_sounds {
// Built-in sound played when the eyes startle in this theme, or nullptr for none.
const char* defaultFor(const char* themeId);
}  // namespace theme_sounds
