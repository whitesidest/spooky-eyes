# Spooky Eyes — Plan

Animated, Home-Assistant-controlled eyes for the Waveshare **ESP32-S3-DualEye(-Touch)-LCD-1.28**
(and any number of them). One repo, three parts:

```
firmware/                 PlatformIO (pioarduino / Arduino-ESP32 v3, IDF 5) firmware
  src/render/             portable C++ renderer + behaviour engine (no Arduino deps)
tools/preview/            native build of the renderer -> PNG/GIF previews (no hardware needed)
custom_components/spooky_eyes/   HACS integration (domain: spooky_eyes)
webapp/                   Django web controller (fleet dashboard, live gaze pad, groups/scenes)
tests/                    pytest suite for the integration
hacs.json
```

## Hardware (from the Waveshare wiki)

| Signal | LCD1 (left) | LCD2 (right) |
|---|---|---|
| SCLK / MOSI / MISO | 41 / 42 / 40 (shared) | shared |
| DC | 45 (shared) | 45 (shared) |
| CS | 47 | 38 |
| RST | 48 | 8 |
| BL (PWM) | 46 | 39 |

Controller GC9A01A, 240×240 RGB565. ESP32-S3R8: 8 MB octal PSRAM, 16 MB flash.
Touch (touch variant only): CST816, I2C SDA 11 / SCL 10, INT 5 / 7, RST 4 / 6.
Audio: ES8311 + I2S (MCLK 12, SCLK 13, LRCK 14, DOUT 15, DIN 16). Battery ADC: GPIO1.
Pin map lives in one header (`firmware/src/board.h`) so other boards can be added.

## Architecture

**Rendering** is procedural, not bitmap-based. Each theme is a `ThemeSpec` (data): sclera,
iris gradient + striations, pupil shape (round / slit / none), fire field (rising / radial),
glow + pulse, specular, eyelids, scanlines, and motion personality (saccade rate, blink rate,
gaze range, snap vs. ease). Adding a theme = adding a struct (later: JSON pushed from HA).

The renderer shades rows into strips (240×N RGB565) which are DMA'd to the panel while the
next strip renders — no full framebuffers needed. Both panels share one SPI bus; DC is set per
transaction in the pre-transfer callback.

**Behaviour engine** (`EyeController`) produces a per-eye `EyeState` each frame: autonomous
saccades, micro-jitter, Poisson blinks, pupil "breathing", moods (neutral / angry / surprised /
sleepy / asleep), and overrides from HA (look-at, blink, wink, sleep).

The renderer + controller are portable C++, compiled natively in `tools/preview` so themes can
be designed and reviewed as images without hardware.

## Device API (contract between firmware and HA)

Discovery: mDNS hostname `spooky-eyes-<last 6 hex of MAC>`, service `_spookyeyes._tcp`, port 80,
TXT: `id=<mac hex>`, `model=dualeye-1.28`, `fw=<version>`.

| Method | Path | Body / Response |
|---|---|---|
| GET | `/api/info` | `{id, name, model, fw, mac, ip, themes:[{id,name}], moods:[...], eyes:2}` |
| GET | `/api/state` | state object (below) |
| POST | `/api/state` | partial state object → returns full state |
| POST | `/api/action` | `{action, ...}` → `{ok:true}` |
| GET (WS) | `/ws` | server pushes `{"type":"state","state":{...}}` on every change; client may send `{"type":"state",...partial}` or `{"type":"action",...}` |
| POST | `/update` | OTA firmware upload (multipart) |

State object:
```json
{
  "on": true,               // displays + backlight on
  "brightness": 200,        // 0-255 backlight
  "theme": "sauron",
  "mood": "neutral",        // neutral | angry | surprised | sleepy | asleep
  "autonomous": true,       // idle animation (saccades, blinks)
  "pupil": null,            // null = auto, else 0.0-1.0 dilation override
  "gaze": {"x": 0.0, "y": 0.0}, // last commanded gaze, -1..1 (read-only while autonomous)
  "rssi": -58, "fps": 31.5, "uptime": 1234
}
```
Actions: `blink`, `wink_left`, `wink_right`, `look` (`x`,`y` in -1..1, `duration` s, 0 = hold
until released), `release` (return to autonomous gaze), `startle`, `roll` (eye roll).

## Home Assistant integration (`spooky_eyes`, HACS)

- Config flow: zeroconf auto-discovery + manual host entry. One config entry per device.
- `local_push`: WebSocket subscription pushes state; REST for commands; auto-reconnect.
- Entities per device:
  - `light` — on/off + brightness (backlight)
  - `select` — theme, mood
  - `switch` — idle animation (autonomous)
  - `number` — pupil dilation override (with "auto" via switch/attr)
  - `button` — blink, wink left, wink right, startle, eye roll
  - `sensor` (diagnostic) — Wi-Fi RSSI, FPS, uptime
- Services (target devices/entities, fan-out to many boards): `spooky_eyes.look`,
  `spooky_eyes.action`.
- Many boards = many devices; HA groups/scenes/automations drive them together
  (e.g. motion sensor → every pair of eyes looks toward the front door and turns angry).

## Web controller (`webapp/`, Python + Django)

A standalone companion to HA for hands-on control of the whole fleet (HA stays the automation
brain). The device itself serves only the JSON API; all UI lives here.

- Device registry: zeroconf discovery (`_spookyeyes._tcp`) + manual add; online status.
- Dashboard: every board as a card — power, brightness, theme, mood, idle toggle, action buttons.
- Live gaze pad: drag to steer one board or a whole group in real time (proxied over the device WS).
- Groups & scenes: named sets of boards, one-click scenes ("all Sauron, angry, looking at door").
- Talks to devices over the same REST/WS API as HA; no extra firmware endpoints needed.
- Later: theme designer with live preview once firmware accepts JSON custom themes.

## Themes (v1)

| id | Look | Personality |
|---|---|---|
| `human` | realistic hazel iris, veined sclera, eyelids | baseline, natural blinks |
| `cat` | huge yellow-green iris, vertical slit that dilates | quick darting, slow blinks |
| `fire` | burning iris, flames rising off it, charred lids | restless |
| `alien` | glossy black almond, iridescent swirl, huge pupil | twitchy, rarely blinks |
| `sauron` | lidless fiery radial iris, black slit pupil | slow searching sweeps, never blinks |
| `terminator` | red glowing core in a metal lens housing, scanlines | mechanical snaps, flickers instead of blinks |

Added (v1.1), grouped by `category` (reported in `/api/info` themes):

| Category | Themes |
|---|---|
| halloween | `fire` (flaming pits, no pupil), `sauron` (lidless, slow searchlight sweeps), `zombie` (bloodshot; right eye is a milky, wall-eyed cataract), `demon` (goat bar pupil, flames), `werewolf` (amber eyeshine), `vampire` (crimson glow), `ghost` (luminous ring with ectoplasm wisps), `jack_o_lantern` (flame-filled carved triangle), `spider` (four glossy red orbs per panel), `dead` (cartoon X_X, sickly green) |
| creatures | `cat` (slow trusting blinks), `dragon` (gold slit, scaly lids), `owl` (huge pupils, locks on), `snake` (lidless copper slit, never blinks), `chameleon` (scaly turrets, each eye wanders independently) |
| sci-fi | `alien` (eyes drift out of sync), `terminator` (target-acquisition sweeps), `robot` (blue lens, aperture pupil) |
| holidays | `frost` (winter ice, glints), `valentine` (heart pupil, heartbeat glow), `st_patricks` (shamrock pupil, gold rim), `easter` (pastel candy iris), `fireworks` (star core, red/white/blue sparks) |
| fun | `hypnotic` (spinning spiral), `rainbow` (hue-cycling iris), `puppy` (droopy lids that doze off and jolt awake), `anime` (huge glossy iris, twinkling stars) |
| classic | `human` |

Theme extras (all optional, zero = off): `sparkle`, `lidDroop`, `blinkSpeed`, `independence`
(right eye picks its own targets while idle; both converge on `look`), `scanRate` (slow full-width
sweeps per minute), `dozeRate` (lids sag shut then snap open), `cluster` (sub-eyes per panel from a
built-in layout), `pupilDilate` (startle dilation), `fireStretch` (radial streaks), and `right`
(an `EyeVariant`: iris/pupil/glow colours, pupil and glow scale, cataract `haze`, wall-eye `lazy`).
Pupil shapes: round, slit, bar, heart, triangle, star, clover, cross (X), none.

Ideas next: `pumpkin_king`, JSON custom themes from HA / the web controller.

## Phases

1. **Now (no hardware):** repo scaffold, renderer + themes + preview tool, firmware skeleton
   (display driver, render loop, Wi-Fi, API, mDNS, OTA) compiling, HA integration + tests.
2. **Bring-up:** flash, verify panels / orientation / SPI clock, measure FPS, tune performance
   (half-res fire fields, split rendering across both cores if needed).
3. **HA polish:** HACS repo, release workflow, blueprints (motion → look, doorbell → startle).
4. **Web controller:** Django app (above).
5. **Extras:** touch "poke" reactions, ES8311 sound effects, JSON custom themes, synced
   multi-board choreography, web UI on the device.
