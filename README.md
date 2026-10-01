# Spooky Eyes

Animated eyes for the Waveshare **ESP32-S3-DualEye(-Touch)-LCD-1.28** — fire, cat, alien, Sauron,
Terminator and more — controlled from Home Assistant. Run as many boards as you like; each shows
up as its own device.

- `firmware/` — PlatformIO firmware for the board
- `custom_components/spooky_eyes/` — Home Assistant integration (HACS)
- `webapp/` — Django web controller: phone-first remote with a visual theme gallery, a Puppeteer
  gaze pad for live scaring, and scenes & groups (see [webapp/README.md](webapp/README.md))
- `tools/preview/` — render themes to images without hardware

See [PLAN.md](PLAN.md) for the architecture and device API.

## Firmware

Requires [PlatformIO](https://platformio.org/).

```bash
cd firmware
pio run -e dualeye -t upload --upload-port /dev/ttyACM0   # USB
pio device monitor
```

1. On first boot the board opens a Wi-Fi setup portal named `SpookyEyes-xxxxxx`. Join it
   and pick your network (the eyes animate the whole time).
2. Once connected it announces itself over mDNS as `spooky-eyes-xxxxxx.local`
   (`_spookyeyes._tcp`). Home Assistant discovers it automatically.
3. Later updates can go over the network:
   `pio run -e dualeye_ota -t upload --upload-port spooky-eyes-xxxxxx.local`

Themes (32): Halloween — fire, Sauron, zombie (one dead eye), demon, werewolf, vampire, ghost,
jack-o'-lantern, spider (eight eyes), dead (cartoon X_X), killer doll (glassy blue doll eyes that
snap), puppet (hollow socket, red eye, cheek spiral), bloodshot zombie (throbbing blood-red eyes),
witch (poison-green slit eye in a violet socket, cauldron flames, scheming squint);
creatures — cat, dragon, owl, snake, chameleon (independent
eyes); sci-fi — alien, Terminator, robot; holidays — frost, valentine, St. Patrick's, Easter,
fireworks; fun — hypnotic, rainbow, sleepy puppy, anime; classic — human. Preview them without
hardware:

```bash
python3 tools/preview/render.py all --frames 60            # -> tools/preview/out/*.gif + contact sheet
python3 tools/preview/render.py sauron --mood angry --action startle --stills   # one theme, one action
```

## Home Assistant integration

### Install

1. HACS → Integrations → ⋮ → **Custom repositories** → add
   `https://github.com/whitesidest/spooky-eyes` as an **Integration**.
2. Install **Spooky Eyes** and restart Home Assistant.
3. Boards on your network are discovered automatically (zeroconf `_spookyeyes._tcp`) — accept the
   notification. Or **Settings → Devices & services → Add integration → Spooky Eyes** and enter the
   board's host (`spooky-eyes-a1b2c3.local` or its IP).

State is pushed over a WebSocket (`local_push`), so changes made on the board or by another
client show up instantly; a 60 s poll backs it up if the socket drops.

### What you get (per board)

| Entity | Does |
|---|---|
| `light.<board>` | On/off blanks both panels; brightness drives the backlight |
| `select.<board>_theme` | Theme (options come from the board) |
| `select.<board>_mood` | neutral / angry / surprised / sleepy / asleep |
| `switch.<board>_idle_animation` | Autonomous glances and blinks |
| `number.<board>_pupil_dilation` | Pupil override, 0–100 % (unknown = automatic) |
| Buttons | Blink, Wink left, Wink right, Startle, Eye roll, Auto pupil |
| Diagnostic sensors | Wi-Fi signal, Frame rate, Uptime (disabled by default) |
| `number.<board>_volume` | Speaker volume, 0–100 % *(boards with a speaker)* |
| `button.<board>_stop_sound` | Stop whatever is playing *(speaker)* |
| `media_player.<board>_voice` | The talking skull: play a built-in effect or clip, or any audio HA can produce — TTS, a media-source file, a URL — which is converted and sent to the board *(speaker; see below)* |
| `switch.<board>_theme_sounds` | Every theme plays its paired sound when the eyes startle *(firmware 0.3+ with a speaker)* |
| `select.<board>_startle_sound` | The sound paired with the current theme: none, a built-in or an uploaded clip *(firmware 0.3+ with a speaker)* |
| `switch.<board>_react_to_noise` | Eyes jump and glance toward loud noises *(microphones)* |
| `number.<board>_noise_sensitivity` | How quiet a noise still counts, 0–100 % *(microphones)* |
| `event.<board>_noise` | Fires on every loud noise (attribute `direction`: -1 left … +1 right) *(microphones)* |
| `sensor.<board>_battery` | Battery % (unknown with no cell); voltage as a diagnostic sensor *(battery)* |
| `sensor.<board>_sound_level` | Microphone level, dBFS (diagnostic) *(microphones)* |

### Services

Both target devices or entities, so one call can drive every board at once.

- `spooky_eyes.look` — `x`, `y` in -1…1 (x: -1 = viewer's left; y: -1 = up), `duration` seconds
  (0 = hold until released).
- `spooky_eyes.action` — `action`: `blink`, `wink_left`, `wink_right`, `look`, `release`,
  `startle`, `roll` (+ optional `x`, `y`, `duration` for `look`).
- `spooky_eyes.play_sound` — `sound`: a built-in effect (`growl`, `heartbeat`, `whisper`, `creak`,
  `zap`, `chime`, `test`) or the name of a clip uploaded from the web controller.
- `spooky_eyes.stop_sound`

Every loud noise also fires a `spooky_eyes_noise` event on the HA bus (`board_id`, `name`, `direction`).

### The talking skull

`media_player.<board>_voice` makes the board a speaker for the rest of Home Assistant. Built-in
effects and uploaded clips play straight from the board (pick them in the media browser, or use
`media_content_id: spooky_eyes://sound/growl` / a bare name with `media_content_type: sound`).
Anything else — `tts.speak`, a file from a media source, an `http(s)` URL — is downloaded, converted
to 16 kHz mono WAV with ffmpeg (Home Assistant's `ffmpeg` integration if it is set up, else an
`ffmpeg` on the PATH; the official images have one), uploaded to the board as the clip `tts`
(overwritten each time, up to 60 s) and played. Announcements work the same way; volume set/step and
stop are supported. State is `playing` while the board plays anything, `idle` otherwise, `off` when
the eyes are off.

```yaml
# Doorbell: the skull greets whoever is there.
automation:
  - alias: Skull greets visitors
    triggers:
      - trigger: state
        entity_id: binary_sensor.doorbell
        to: "on"
    actions:
      - action: tts.speak
        target:
          entity_id: tts.google_translate_en_com
        data:
          media_player_entity_id: media_player.porch_eyes_voice
          message: "Welcome. We've been expecting you."
```

Firmware that pairs a sound with each theme also gets `switch.<board>_theme_sounds` (master enable)
and `select.<board>_startle_sound`: the sound played whenever the eyes startle — a loud noise, the
Startle button, an automation — for the theme currently showing. Pick "None" to clear the pairing.
The select's options follow the board's sound list (refreshed every minute); `default_sound` in its
attributes is what the theme ships with.

```yaml
# Front-door motion: every pair of eyes snaps to the door and gets angry.
automation:
  - alias: Eyes watch the door
    triggers:
      - trigger: state
        entity_id: binary_sensor.front_door_motion
        to: "on"
    actions:
      - action: spooky_eyes.look
        target:
          device_id: [porch_eyes_device_id, window_eyes_device_id]
        data: {x: -0.8, y: 0.1, duration: 8}
      - action: select.select_option
        target:
          entity_id: [select.porch_eyes_mood, select.window_eyes_mood]
        data: {option: angry}
```

```yaml
# Something bangs near the skull: it growls back.
automation:
  - alias: Skull growls at noises
    triggers:
      - trigger: state
        entity_id: event.porch_eyes_noise
    actions:
      - action: spooky_eyes.play_sound
        target:
          entity_id: light.porch_eyes
        data: {sound: growl}
```

### Development

```bash
uv venv -p 3.13 .venv && uv pip install -p .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```
