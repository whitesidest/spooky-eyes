#include "net.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <ArduinoOTA.h>
#include <AsyncJson.h>
#include <ESPAsyncWebServer.h>
#include <ESPmDNS.h>
#include <Update.h>
#include <WiFi.h>
#include <esp_mac.h>

#include "board.h"
#include "engine.h"
#include "provision.h"

namespace net {
namespace {

constexpr uint32_t kPeriodicPushMs = 15000;

AsyncWebServer server(80);
AsyncWebSocket ws("/ws");
bool online = false;
uint32_t pushedVersion = 0;
uint32_t lastPushMs = 0;
String hostname;

String stateMessage() {
  JsonDocument doc;
  doc["type"] = "state";
  engine::writeState(doc["state"].to<JsonObject>());
  String out;
  serializeJson(doc, out);
  return out;
}

void sendJson(AsyncWebServerRequest* req, int code, JsonDocument& doc) {
  String body;
  serializeJson(doc, body);
  req->send(code, "application/json", body);
}

void sendError(AsyncWebServerRequest* req, const String& error) {
  JsonDocument doc;
  doc["error"] = error;
  sendJson(req, 400, doc);
}

void handleWsMessage(AsyncWebSocketClient* client, const char* data, size_t len) {
  JsonDocument doc;
  if (deserializeJson(doc, data, len)) return;
  const char* type = doc["type"] | "";
  String error;
  bool ok = true;
  if (!strcmp(type, "state")) {
    // Accept {"type":"state","state":{...}} or the fields at top level.
    JsonVariantConst body = doc["state"].is<JsonObject>() ? doc["state"].as<JsonVariantConst>() : doc.as<JsonVariantConst>();
    JsonDocument copy;
    copy.set(body);
    copy.remove("type");
    ok = engine::applyState(copy.as<JsonVariantConst>(), &error);
  } else if (!strcmp(type, "action")) {
    ok = engine::applyAction(doc.as<JsonVariantConst>(), &error);
  } else {
    return;
  }
  if (!ok) client->text(String("{\"type\":\"error\",\"error\":\"") + error + "\"}");
}

void onWsEvent(AsyncWebSocket*, AsyncWebSocketClient* client, AwsEventType type, void* arg, uint8_t* data, size_t len) {
  if (type == WS_EVT_CONNECT) {
    client->text(stateMessage());
  } else if (type == WS_EVT_DATA) {
    auto* info = (AwsFrameInfo*)arg;
    if (info->final && info->index == 0 && info->len == len && info->opcode == WS_TEXT)
      handleWsMessage(client, (const char*)data, len);
  }
}

void setupRoutes() {
  server.on("/api/info", HTTP_GET, [](AsyncWebServerRequest* req) {
    JsonDocument doc;
    engine::writeInfo(doc.to<JsonObject>());
    sendJson(req, 200, doc);
  });
  server.on("/api/debug", HTTP_GET, [](AsyncWebServerRequest* req) {
    JsonDocument doc;
    engine::writeDebug(doc.to<JsonObject>());
    sendJson(req, 200, doc);
  });
  server.on("/api/state", HTTP_GET, [](AsyncWebServerRequest* req) {
    JsonDocument doc;
    engine::writeState(doc.to<JsonObject>());
    sendJson(req, 200, doc);
  });
  server.addHandler(new AsyncCallbackJsonWebHandler("/api/state", [](AsyncWebServerRequest* req, JsonVariant& json) {
    String error;
    if (!engine::applyState(json, &error)) return sendError(req, error);
    JsonDocument doc;
    engine::writeState(doc.to<JsonObject>());
    sendJson(req, 200, doc);
  }));
  server.addHandler(new AsyncCallbackJsonWebHandler("/api/action", [](AsyncWebServerRequest* req, JsonVariant& json) {
    String error;
    if (!engine::applyAction(json, &error)) return sendError(req, error);
    req->send(200, "application/json", "{\"ok\":true}");
  }));
  server.on(
      "/update", HTTP_POST,
      [](AsyncWebServerRequest* req) {
        bool ok = !Update.hasError();
        req->send(ok ? 200 : 500, "application/json", ok ? "{\"ok\":true}" : "{\"ok\":false}");
        if (ok) {
          delay(200);
          ESP.restart();
        }
      },
      [](AsyncWebServerRequest*, const String&, size_t index, uint8_t* data, size_t len, bool final) {
        if (index == 0) Update.begin(UPDATE_SIZE_UNKNOWN);
        if (Update.write(data, len) != len) Update.printError(Serial);
        if (final && !Update.end(true)) Update.printError(Serial);
      });
  server.on("/", HTTP_GET, [](AsyncWebServerRequest* req) {
    req->send(200, "text/plain", "Spooky Eyes " FW_VERSION " - API at /api/info, /api/state, /api/action, /ws\n");
  });
  server.onNotFound([](AsyncWebServerRequest* req) { req->send(404, "application/json", "{\"error\":\"not found\"}"); });
  ws.onEvent(onWsEvent);
  server.addHandler(&ws);
}

void goOnline() {
  online = true;
  Serial.printf("Wi-Fi connected: %s  http://%s.local\n", WiFi.localIP().toString().c_str(), hostname.c_str());
  MDNS.begin(hostname.c_str());
  MDNS.addService("spookyeyes", "tcp", 80);
  MDNS.addServiceTxt("spookyeyes", "tcp", "id", engine::deviceId());
  MDNS.addServiceTxt("spookyeyes", "tcp", "model", BOARD_MODEL);
  MDNS.addServiceTxt("spookyeyes", "tcp", "fw", FW_VERSION);
  server.begin();
  ArduinoOTA.setHostname(hostname.c_str());
  ArduinoOTA.setMdnsEnabled(false);  // already running
  ArduinoOTA.begin();
}

}  // namespace

void begin() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // lower latency for live gaze control
  // Read the station MAC from eFuse: WiFi.macAddress() can return zeros before the netif is up.
  uint8_t raw[6];
  esp_read_mac(raw, ESP_MAC_WIFI_STA);
  char hex[13];
  snprintf(hex, sizeof hex, "%02x%02x%02x%02x%02x%02x", raw[0], raw[1], raw[2], raw[3], raw[4], raw[5]);
  String mac = hex;
  engine::setIdentity(mac);
  hostname = "spooky-eyes-" + mac.substring(6);
  WiFi.setHostname(hostname.c_str());
  setupRoutes();

  String ap = "SpookyEyes-" + mac.substring(6);
  if (provision::begin(ap.c_str(), hostname.c_str())) goOnline();
  else Serial.printf("Setup portal: join Wi-Fi \"%s\" and open http://192.168.4.1\n", ap.c_str());
}

void loop() {
  if (!online) {
    if (provision::process()) goOnline();
    return;
  }
  ArduinoOTA.handle();
  uint32_t v = engine::stateVersion();
  if ((v != pushedVersion || millis() - lastPushMs > kPeriodicPushMs) && ws.count()) {
    ws.textAll(stateMessage());
    pushedVersion = v;
    lastPushMs = millis();
  }
  ws.cleanupClients();
}

}  // namespace net
