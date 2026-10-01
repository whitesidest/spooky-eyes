"""Constants for the Spooky Eyes integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "spooky_eyes"
MANUFACTURER = "Waveshare"
DEFAULT_PORT = 80
FALLBACK_SCAN_INTERVAL = timedelta(seconds=60)

PLATFORMS = [
    Platform.BUTTON,
    Platform.EVENT,
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

DEFAULT_MOODS = ["neutral", "angry", "surprised", "sleepy", "asleep"]

# Actions accepted by POST /api/action (see PLAN.md, "Device API").
ACTION_BLINK = "blink"
ACTION_WINK_LEFT = "wink_left"
ACTION_WINK_RIGHT = "wink_right"
ACTION_LOOK = "look"
ACTION_RELEASE = "release"
ACTION_STARTLE = "startle"
ACTION_ROLL = "roll"
ACTION_SOUND = "sound"
ACTION_STOP_SOUND = "stop_sound"
ACTIONS = [
    ACTION_BLINK,
    ACTION_WINK_LEFT,
    ACTION_WINK_RIGHT,
    ACTION_LOOK,
    ACTION_RELEASE,
    ACTION_STARTLE,
    ACTION_ROLL,
]

# Sounds: the built-in effects every speaker board has, the clip the media player uploads
# spoken/streamed audio to, and the browse-tree ids it hands Home Assistant.
BUILTIN_SOUNDS = ["growl", "heartbeat", "whisper", "creak", "zap", "chime", "test"]
SOUND_NAME_RE = r"^[a-z0-9_-]{1,24}$"
TTS_CLIP = "tts"
MEDIA_SOUND_PREFIX = "spooky_eyes://sound/"
MEDIA_FOLDER_PREFIX = "spooky_eyes://sounds/"
# "none" stands for "no sound paired" in the startle-sound select (the board uses null).
THEME_SOUND_NONE = "none"

SERVICE_LOOK = "look"
SERVICE_ACTION = "action"
SERVICE_PLAY_SOUND = "play_sound"
SERVICE_STOP_SOUND = "stop_sound"
ATTR_SOUND = "sound"

# Fired on the HA bus (and the "noise" event entity) when a board hears a loud noise.
EVENT_NOISE = f"{DOMAIN}_noise"
ATTR_X = "x"
ATTR_Y = "y"
ATTR_DURATION = "duration"
ATTR_ACTION = "action"


def has_feature(info: dict, name: str) -> bool:
    """Boards on older firmware don't report features (and lack these capabilities)."""
    return bool((info.get("features") or {}).get(name))
