"""Fan-out of commands to many boards in parallel, plus registration/refresh/rename."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from django.utils import timezone

from . import audio, client
from .models import Device, Group
from .validation import BUILTIN_SOUNDS

MAX_WORKERS = 16


class TargetError(ValueError):
    pass


def normalize_id(raw: str) -> str:
    return (raw or "").replace(":", "").replace("-", "").lower()


def resolve_targets(target) -> list[Device]:
    """target: {"device": id} | {"group": pk} | {"all": true}"""
    if not isinstance(target, dict):
        raise TargetError("target must be an object")
    if target.get("all"):
        return list(Device.objects.all())
    if "device" in target:
        devices = list(Device.objects.filter(device_id=normalize_id(str(target["device"]))))
        if not devices:
            raise TargetError("unknown device")
        return devices
    if "group" in target:
        try:
            group = Group.objects.get(pk=target["group"])
        except (Group.DoesNotExist, ValueError, TypeError):
            raise TargetError("unknown group") from None
        return list(group.devices.all())
    raise TargetError("target needs device, group or all")


def _absorb_state(dev: Device, state: dict) -> list[str]:
    """Store a full state object; boards with the name contract also report their name here."""
    fields = ["last_state"]
    dev.last_state = state
    name = state.get("name")
    if isinstance(name, str) and name.strip() and not dev.name_is_local and name != dev.name:
        dev.name = name.strip()
        fields.append("name")
    return fields


def fan_out(devices: list[Device], call) -> dict[str, dict]:
    """Run call(device) for every device concurrently; returns {device_id: {ok, state|error}}."""
    if not devices:
        return {}

    def run(dev: Device):
        try:
            return dev, call(dev), None
        except client.DeviceError as err:
            return dev, None, str(err)

    results = {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(devices))) as pool:
        for dev, value, error in pool.map(run, devices):
            if error:
                results[dev.device_id] = {"ok": False, "error": error}
                continue
            fields = ["last_seen"]
            dev.last_seen = timezone.now()
            if isinstance(value, dict) and "theme" in value:  # a full state object
                fields += _absorb_state(dev, value)
            dev.save(update_fields=fields)
            results[dev.device_id] = {"ok": True, "state": dev.last_state}
    return results


def apply_state(devices, state: dict):
    return fan_out(devices, lambda d: client.set_state(d.host, d.port, state))


def apply_action(devices, payload: dict):
    return fan_out(devices, lambda d: client.action(d.host, d.port, payload))


def refresh(devices):
    return fan_out(devices, lambda d: client.get_state(d.host, d.port))


def register(host: str, port: int = 80) -> Device:
    """Validate a board by GET /api/info and upsert it. Raises client.DeviceError."""
    info = client.get_info(host, port)
    device_id = normalize_id(info.get("id", ""))
    if len(device_id) != 12:
        raise client.DeviceError(f"{host}: /api/info has no valid id")
    defaults = {
        "host": host,
        "port": port,
        "model": info.get("model", ""),
        "fw": info.get("fw", ""),
        "info": info,
        "last_seen": timezone.now(),
    }
    existing = Device.objects.filter(device_id=device_id).first()
    if not (existing and existing.name_is_local):  # a locally chosen name survives rediscovery
        defaults["name"] = info.get("name") or f"Spooky Eyes {device_id[-6:]}"
    device, _ = Device.objects.update_or_create(device_id=device_id, defaults=defaults)
    try:
        device.last_state = client.get_state(host, port)
        device.save(update_fields=["last_state"])
    except client.DeviceError:
        pass
    if device.features["speaker"]:
        try:
            fetch_sounds(device)
        except client.DeviceError:
            pass
    return device


# --- Sounds ---

def fetch_sounds(device: Device) -> dict:
    """GET the board's sound library and remember it (for scenes and group sound strips)."""
    listing = _store_sounds(device, client.get_sounds(device.host, device.port))
    return listing


def _store_sounds(device: Device, listing: dict) -> dict:
    listing = {
        "builtin": [s for s in listing.get("builtin", []) if isinstance(s, str)],
        "clips": [c for c in listing.get("clips", []) if isinstance(c, dict) and isinstance(c.get("name"), str)],
        "free_bytes": int(listing.get("free_bytes") or 0),
    }
    device.sounds = listing
    device.last_seen = timezone.now()
    device.save(update_fields=["sounds", "last_seen"])
    return listing


class UploadError(ValueError):
    pass


def upload_sound(device: Device, name: str, data: bytes, filename: str = "") -> dict:
    """Convert `data` to 16 kHz mono WAV, check it fits, send it. Returns the new listing.

    Raises UploadError (bad file / too big, a 400 for the caller) or client.DeviceError.
    """
    try:
        wav = audio.convert(data, filename)
    except audio.AudioError as err:
        raise UploadError(str(err)) from None
    seconds = audio.wav_seconds(wav)
    if seconds < 0.05:
        raise UploadError("that clip is silent or too short")
    try:
        listing = fetch_sounds(device)
    except client.DeviceError:
        listing = device.sounds or {}
    free = int(listing.get("free_bytes") or 0)
    replacing = next((c.get("bytes", 0) for c in listing.get("clips", []) if c.get("name") == name), 0)
    if free and len(wav) > free + replacing:
        fits = (free + replacing) / audio.BYTES_PER_SECOND
        raise UploadError(
            f"{seconds:.0f} s of audio is {audio.human_size(len(wav))}, but the board only has "
            f"{audio.human_size(free + replacing)} free (about {fits:.0f} s). Trim the clip or delete another one."
        )
    return _store_sounds(device, client.upload_sound(device.host, device.port, name, wav))


def delete_sound(device: Device, name: str) -> dict:
    client.delete_sound(device.host, device.port, name)
    return fetch_sounds(device)


def common_sounds(devices: list[Device]) -> dict:
    """Built-ins and clips every speaker-equipped board in `devices` has (for group targets)."""
    able = [d for d in devices if d.features["speaker"]]
    if not able:
        return {"builtin": [], "clips": [], "any": False}
    builtin = None
    clips = None
    for d in able:
        b = set(d.sounds.get("builtin") or [])
        c = {x["name"] for x in d.sounds.get("clips") or []}
        builtin = b if builtin is None else builtin & b
        clips = c if clips is None else clips & c
    order = [s for s in BUILTIN_SOUNDS if s in builtin] + sorted(builtin - set(BUILTIN_SOUNDS))
    return {"builtin": order, "clips": sorted(clips), "any": True}


def rename(device: Device, name: str) -> dict:
    """Rename a board. Firmware with the name contract stores it; otherwise the name is kept here.

    Returns {"on_board": bool, "note": str|None}.
    """
    note = "this board's firmware doesn't keep names, so it's saved here"
    try:
        state = client.set_state(device.host, device.port, {"name": name})
    except client.DeviceError as err:
        if "HTTP 4" not in str(err):
            note = f"the board didn't answer ({err}), so it's saved here"
        state = None
    if state is not None and state.get("name") == name:
        device.name, device.name_is_local = name, False
        device.last_seen = timezone.now()
        device.info = {**device.info, "name": name}
        device.last_state = state
        device.save(update_fields=["name", "name_is_local", "last_seen", "info", "last_state"])
        return {"on_board": True, "note": None}
    device.name, device.name_is_local = name, True
    device.save(update_fields=["name", "name_is_local"])
    return {"on_board": False, "note": note}


def apply_scene(scene):
    devices = scene.targets()
    results = {}
    if scene.state:
        results["state"] = apply_state(devices, scene.state)
    if scene.action:
        results["action"] = apply_action(devices, scene.action)
    return results
