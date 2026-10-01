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
