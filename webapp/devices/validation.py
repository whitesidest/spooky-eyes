"""Validates payloads before they are fanned out to devices or stored as scenes/groups."""
import re

MOODS = {"neutral", "angry", "surprised", "sleepy", "asleep"}
ACTIONS = {"blink", "wink_left", "wink_right", "look", "release", "startle", "roll", "sound", "tone", "stop_sound"}
NAME_MAX = 32  # firmware limit for the board name
SOUND_NAME_RE = re.compile(r"^[a-z0-9_-]{1,24}$")  # firmware rule for sound names
SOUND_NAME_MAX = 24
BUILTIN_SOUNDS = ["growl", "heartbeat", "whisper", "creak", "zap", "chime", "test"]
VOICE_EFFECTS = ["natural", "demon", "ghost", "robot"]  # see audio.EFFECTS
VOICE_CLIP = "voice"  # the clip name push-to-talk and "play from URL" (over)write on the board


class ValidationError(ValueError):
    pass


def _num(value, lo, hi, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise ValidationError(f"{name} must be a number {lo}..{hi}")
    return value


def clean_state(state) -> dict:
    if not isinstance(state, dict) or not state:
        raise ValidationError("state must be a non-empty object")
    out = {}
    for key, value in state.items():
        if key in ("on", "autonomous", "listen", "theme_sounds"):
            if not isinstance(value, bool):
                raise ValidationError(f"{key} must be boolean")
        elif key == "theme_sound":
            # The sound paired with the current theme; null clears the pairing.
            if value is not None:
                value = clean_sound_name(value)
        elif key == "brightness":
            value = int(_num(value, 0, 255, key))
        elif key in ("volume", "sensitivity"):
            value = int(_num(value, 0, 100, key))
        elif key == "theme":
            if not isinstance(value, str) or not value:
                raise ValidationError("theme must be a theme id")
        elif key == "mood":
            if value not in MOODS:
                raise ValidationError(f"mood must be one of {sorted(MOODS)}")
        elif key == "pupil":
            if value is not None:
                _num(value, 0, 1, key)
        else:
            raise ValidationError(f"unknown state field {key!r}")
        out[key] = value
    return out


def clean_sound_name(value) -> str:
    """A sound (built-in or clip) name: 1-24 of a-z 0-9 _ -."""
    if not isinstance(value, str) or not SOUND_NAME_RE.match(value):
        raise ValidationError("sound name must be 1-24 characters of a-z, 0-9, _ or -")
    return value


def clean_effect(value) -> str:
    """A voice effect name; empty means natural."""
    if value in (None, ""):
        return "natural"
    if value not in VOICE_EFFECTS:
        raise ValidationError(f"effect must be one of {VOICE_EFFECTS}")
    return value


def clean_url(value) -> str:
    """An http(s) URL for "play from URL"."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("url is required")
    value = value.strip()
    if not re.match(r"^https?://[^\s/]+", value, re.IGNORECASE):
        raise ValidationError("url must start with http:// or https://")
    if len(value) > 2000:
        raise ValidationError("url is too long")
    return value


def suggest_sound_name(filename: str) -> str:
    """Turn an uploaded file's name into a valid sound name ("Big Growl 2.mp3" -> "big_growl_2")."""
    stem = (filename or "").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    stem = re.sub(r"[^a-z0-9_-]+", "_", stem).strip("_-")
    return stem[:SOUND_NAME_MAX] or "clip"


def clean_action(payload) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("action must be an object")
    name = payload.get("action")
    if name not in ACTIONS:
        raise ValidationError(f"action must be one of {sorted(ACTIONS)}")
    out = {"action": name}
    if name == "look":
        out["x"] = float(_num(payload.get("x", 0), -1, 1, "x"))
        out["y"] = float(_num(payload.get("y", 0), -1, 1, "y"))
        out["duration"] = float(_num(payload.get("duration", 0), 0, 3600, "duration"))
    elif name == "sound":
        out["name"] = clean_sound_name(payload.get("name"))
    elif name == "tone":
        out["hz"] = float(_num(payload.get("hz", 440), 20, 8000, "hz"))
        out["ms"] = int(_num(payload.get("ms", 500), 10, 10000, "ms"))
    return out


def clean_name(value, what="name", max_length=100) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{what} must be text")
    value = " ".join(value.split())
    if not value:
        raise ValidationError(f"{what} is required")
    if len(value) > max_length:
        raise ValidationError(f"{what} must be at most {max_length} characters")
    return value


def clean_scene(data) -> dict:
    """Returns {name, group (pk or None), state, action (or None)} for a scene create/update."""
    if not isinstance(data, dict):
        raise ValidationError("scene must be an object")
    out = {"name": clean_name(data.get("name"), "scene name")}
    group = data.get("group")
    if group in (None, "", 0, "all"):
        out["group"] = None
    else:
        try:
            out["group"] = int(group)
        except (TypeError, ValueError):
            raise ValidationError("group must be a group id") from None
    state = data.get("state") or {}
    out["state"] = clean_state(state) if state else {}
    action = data.get("action")
    out["action"] = clean_action(action) if action else None
    if not out["state"] and not out["action"]:
        raise ValidationError("a scene needs at least one setting or an action")
    return out


def clean_group(data) -> dict:
    """Returns {name, devices: [device ids]} for a group create/update."""
    if not isinstance(data, dict):
        raise ValidationError("group must be an object")
    devices = data.get("devices", [])
    if not isinstance(devices, list) or not all(isinstance(d, str) for d in devices):
        raise ValidationError("devices must be a list of board ids")
    return {"name": clean_name(data.get("name"), "group name"), "devices": devices}
