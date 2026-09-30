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
ATTR_X = "x"
ATTR_Y = "y"
ATTR_DURATION = "duration"
ATTR_ACTION = "action"
