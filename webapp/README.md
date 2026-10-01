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
| `/boards/<id>/` | **Board** — big picture, power + brightness, theme gallery by category (Halloween first), mood, actions, idle toggle, a **Sound** section (below), an inline gaze pad, "Save this look as a scene", rename, a battery badge next to the status, and diagnostics tucked in a drawer (pupil override, address, firmware, battery, Wi-Fi, fps, uptime, forget). |
| `/puppeteer/` | **Puppeteer** — pick one board / a group / all, a large gaze pad, big Startle/Blink/Eye-roll buttons, winks, release, hold-on-release, mood, and a sound strip (built-ins plus the clips every targeted board has) so you can growl while steering. `?device=<id>` or `?group=<pk>` preselects a target. (`/gaze/` redirects here.) |
| `/scenes/` | **Scenes & groups** — create, edit, delete and apply scenes (theme, mood, power, idle, brightness, volume, a look, a follow-up action — an eye action or a sound — aimed at a group or all boards) and groups (name + member boards). |

## Sound and battery

Firmware with a speaker reports `features: {speaker, microphone, battery}` in `/api/info`; the app shows
the Sound section only when `speaker` is true, the "React to noise" toggle, sensitivity slider and
live room-noise meter only when `microphone` is true, and the battery badge only while the state
carries a `battery` object (boards on USB report `null`). Older firmware without any of this just
gets the old screens.

- **Sound board** — the seven built-in effects (growl, heartbeat, whisper, creak, zap, chime, test
  tone) and every uploaded clip as tap-to-play tiles, a Stop button, and the volume slider.
- **Clips** — "Add clip" takes any common audio file when `ffmpeg` is on the server, otherwise PCM
  WAV only (8/16/24/32-bit, any rate or channel count), converted here to 16 kHz mono 16-bit WAV
  (about 32 kB per second) before it goes to the board. Names follow the firmware rule (1–24 of
  `a-z 0-9 _ -`, suggested from the file name) and an upload that would not fit the board's free
  space is refused with a message saying how much would. "Edit clips" reveals delete marks. The
  board has roughly 3.4 MB for clips (about 100 s).
- **React to noise** — the firmware startles and glances toward loud sounds; sensitivity 0–100 maps
  to a threshold from −18 dBFS (only very loud) to −58 dBFS (whispers). The meter shows the live
  mic level with a tick at that threshold; it polls the board about three times a second but only
  while it is on screen.
- **Battery** — percent and voltage on each fleet card (red under 20 %), beside the board's status
  line, and in diagnostics.

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
(default `*`), `DEVICE_TIMEOUT` seconds (default `3`), `DEVICE_UPLOAD_TIMEOUT` seconds for clip
uploads (default `90`), `MAX_UPLOAD_BYTES` for audio files before conversion (default 40 MB).
Install `ffmpeg` to accept mp3/m4a/ogg/flac clips; without it only WAV files are converted. The Django admin (`/admin/`) still exists
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
| GET | `/api/devices/<device_id>/state` | the board's state right now, straight through (for the mic meter; nothing stored) |
| GET | `/api/devices/<device_id>/sounds` | `{"sounds": {"builtin": [...], "clips": [{"name", "bytes"}], "free_bytes": N}}` (also remembered per board) |
| POST | `/api/devices/<device_id>/sounds` | multipart `file` (+ optional `name`) → converted and uploaded → `{"sounds", "name"}` |
| DELETE | `/api/devices/<device_id>/sounds/<name>` | — |
| POST | `/api/state` | `{"target": T, "state": {...partial}}` — also `volume` 0–100, `listen` bool, `sensitivity` 0–100 |
| POST | `/api/action` | `{"target": T, "action": "look", "x": 0.5, "y": 0, "duration": 1.5}`, `{"action": "sound", "name": "growl"}`, `{"action": "tone", "hz": 440, "ms": 500}`, `{"action": "stop_sound"}` (sound actions aimed at a group skip boards without a speaker) |
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
startle, roll and follow the pad exactly as the hardware will. It supports the rename contract and
the sound/battery API (features, volume/listen/sensitivity, a wandering fake room level with the
odd bang that startles the eyes while listening, in-memory clips with the board's 3.4 MB budget,
a slowly draining battery); `--legacy` emulates older firmware with none of it, `--no-speaker`,
`--no-mic` and `--no-battery` drop single features, `--battery 15` starts low.

```bash
.venv/bin/python fake_device.py --port 8081 --id a1b2c3000001 --name "Porch skull" --theme sauron &
.venv/bin/python fake_device.py --port 8082 --id a1b2c3000002 --name "Window ghoul" --theme zombie --battery 15 --no-mic &
.venv/bin/python fake_device.py --port 8083 --id a1b2c3000003 --name "Old board" --legacy &
# then add 127.0.0.1:8081 and 127.0.0.1:8082 on the home screen
.venv/bin/python manage.py test devices
```

Simulators advertise `"preview": "/sim/frame.png"` in `/api/info`; real boards don't send pictures,
so their cards show the picture of their current theme instead.
