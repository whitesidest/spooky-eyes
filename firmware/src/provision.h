// Wi-Fi provisioning via WiFiManager's captive portal. Kept in its own translation unit because
// WiFiManager's WebServer.h and ESPAsyncWebServer define clashing HTTP_* method enums.
#pragma once

namespace provision {
// Returns true when already connected; otherwise starts the non-blocking portal.
bool begin(const char* apName, const char* hostname);
// Services the portal; returns true once connected (and stops the portal).
bool process();
}  // namespace provision
