"""Validates payloads before they are fanned out to devices."""
MOODS = {"neutral", "angry", "surprised", "sleepy", "asleep"}
ACTIONS = {"blink", "wink_left", "wink_right", "look", "release", "startle", "roll"}


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
        if key in ("on", "autonomous"):
            if not isinstance(value, bool):
                raise ValidationError(f"{key} must be boolean")
        elif key == "brightness":
            value = int(_num(value, 0, 255, key))
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
    return out
