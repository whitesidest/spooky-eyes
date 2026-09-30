#include "provision.h"

#include <WiFi.h>
#include <WiFiManager.h>

namespace provision {
namespace {
WiFiManager wm;
}

bool begin(const char* apName, const char* hostname) {
  // Non-blocking: eyes keep animating while the setup portal is up.
  wm.setConfigPortalBlocking(false);
  wm.setConfigPortalTimeout(0);
  wm.setHostname(hostname);
  return wm.autoConnect(apName);
}

bool process() {
  wm.process();
  if (!WiFi.isConnected()) return false;
  wm.stopConfigPortal();
  return true;
}

}  // namespace provision
