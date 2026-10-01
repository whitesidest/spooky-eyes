# Spooky Eyes — web controller

Django app for hands-on control of every Spooky Eyes board on the LAN, built for a phone in the
dark: a fleet home, a per-board screen with a visual theme gallery, a full-screen "Puppeteer"
gaze pad for live scaring, and scenes & groups managed in the app. Home Assistant remains the
automation brain; this talks to the boards over the same REST API (`/api/info`, `/api/state`,
`/api/action`). Server-rendered Django + vanilla JS/CSS; no build step, no CDN (works offline).

## Screens

| Path | What |
|---|---|
| `/` | **Boards** — every board as a card (picture of its current theme or live view, power, brightness, blink/startle, link to Puppeteer), whole-fleet buttons, one-tap scenes, scan / add by address, and a first-run guide when the list is empty. |
| `/boards/<id>/` | **Board** — big picture, power + brightness, theme gallery by category (Halloween first), mood, actions, idle toggle, an inline gaze pad, "Save this look as a scene", rename, and diagnostics tucked in a drawer (pupil override, address, firmware, Wi-Fi, fps, uptime, forget). |
| `/puppeteer/` | **Puppeteer** — pick one board / a group / all, a large gaze pad, big Startle/Blink/Eye-roll buttons, winks, release, hold-on-release, mood. `?device=<id>` or `?group=<pk>` preselects a target. (`/gaze/` redirects here.) |
| `/scenes/` | **Scenes & groups** — create, edit, delete and apply scenes (theme, mood, power, idle, brightness, a look, a follow-up action, aimed at a group or all boards) and groups (name + member boards). |

## Run

```bash
cd webapp
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 0.0.0.0:8000
```

Add boards with **Scan network** (mDNS `_spookyeyes._tcp`), by address on the home screen
(`10.0.0.5`, `spooky-eyes-a1b2c3.local`, or `host:port`), or from the CLI:

```bash
.venv/bin/python manage.py discover --seconds 5
```

Settings via env: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` (default `1`), `DJANGO_ALLOWED_HOSTS`
(default `*`), `DEVICE_TIMEOUT` seconds (default `3`). The Django admin (`/admin/`) still exists
for poking at the database but nothing requires it.

## Board names

Renaming a board sends `POST /api/state {"name": "Porch skull"}` (1–32 characters). Firmware
that supports it stores the name, reports it in `/api/info` and in every state object, and the
app (and Home Assistant) pick it up. Older firmware answers 400 or ignores the field; the app then
keeps the name locally (`Device.name_is_local`), shows a note on the board screen, and never lets
a rediscovery or refresh overwrite it.

## Theme pictures

Every theme tile, card and scene shows a picture rendered by the real firmware renderer
(`tools/preview`). They live in `devices/static/devices/themes/` (`<id>.png` 500×240 with
transparent corners, `<id>_s.png` 250×120, `<id>.gif` short loop, `themes.json`). Regenerate
after editing `firmware/src/render/themes.cpp` (needs `g++`; GIFs need Pillow, otherwise they're
skipped):

```bash
.venv/bin/python manage.py render_theme_thumbs            # --no-gif, --frames 40, --mood neutral
```

Themes a board reports that have no picture still appear in the gallery with a placeholder.

## JSON API

| Method | Path | Body |
|---|---|---|
| GET | `/api/devices[?refresh=1]` | — (refresh polls every board) |
| POST | `/api/devices` | `{"host": "10.0.0.5", "port": 80}` (or `"host": "10.0.0.5:8081"`) |
| GET | `/api/devices/<device_id>[?refresh=1]` | — |
| PATCH | `/api/devices/<device_id>` | `{"name": "Porch skull"}` → `{"device", "on_board": bool, "note"}` |
| DELETE | `/api/devices/<device_id>` | — |
| GET | `/api/devices/<device_id>/preview` | live picture (simulated boards only) |
| POST | `/api/state` | `{"target": T, "state": {...partial}}` |
| POST | `/api/action` | `{"target": T, "action": "look", "x": 0.5, "y": 0, "duration": 1.5}` |
| GET | `/api/themes` | theme catalogue with picture URLs |
| GET, POST | `/api/scenes` | `{"name", "group": pk|null, "state": {...}, "action": {...}|null}` |
| GET, PUT, DELETE | `/api/scenes/<id>` | same body as POST |
| POST | `/api/scenes/<id>/apply` | — |
| GET, POST | `/api/groups` | `{"name", "devices": ["<device_id>", ...]}` |
| GET, PUT, DELETE | `/api/groups/<id>` | same body as POST |
| POST | `/api/scan` | `{"seconds": 4}` |

`T` is `{"device": "<id>"}`, `{"group": <pk>}` or `{"all": true}`. Commands fan out in parallel
and return `{"results": {"<device_id>": {"ok": true, "state": {...}} | {"ok": false, "error": "..."}}}`.
POST/PUT/PATCH/DELETE need Django's CSRF token (the pages include it).

## Develop without hardware

`fake_device.py` runs the real firmware eye renderer and behaviour engine (built into
`tools/sim/build/libspookysim.so` with g++ on first run), so the app shows live eyes that blink,
startle, roll and follow the pad exactly as the hardware will. It supports the rename contract;
`--legacy` emulates older firmware that rejects it.

```bash
.venv/bin/python fake_device.py --port 8081 --id a1b2c3000001 --name "Porch skull" --theme sauron &
.venv/bin/python fake_device.py --port 8082 --id a1b2c3000002 --name "Window ghoul" --theme zombie --legacy &
# then add 127.0.0.1:8081 and 127.0.0.1:8082 on the home screen
.venv/bin/python manage.py test devices
```

Simulators advertise `"preview": "/sim/frame.png"` in `/api/info`; real boards don't send pictures,
so their cards show the picture of their current theme instead.
