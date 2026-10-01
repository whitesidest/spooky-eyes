import json

from django.db import IntegrityError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from django.conf import settings

from . import audio, client, services, themes
from .models import Device, Group, Scene
from .validation import (BUILTIN_SOUNDS, NAME_MAX, SOUND_NAME_MAX, VOICE_CLIP, VOICE_EFFECTS, ValidationError,
                         clean_action, clean_effect, clean_group, clean_name, clean_scene, clean_sound_name, clean_state,
                         clean_url, suggest_sound_name)

SOUND_ACTIONS = {"sound", "tone", "stop_sound"}

# Kept for callers that imported these from here.
DEFAULT_THEMES = themes.DEFAULT_THEMES
CATEGORY_ORDER = themes.CATEGORY_ORDER


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        raise ValidationError("invalid JSON") from None
    if not isinstance(data, dict):
        raise ValidationError("body must be an object")
    return data


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _device_json(d: Device):
    return {
        "device_id": d.device_id,
        "name": d.name,
        "name_is_local": d.name_is_local,
        "host": d.host,
        "port": d.port,
        "model": d.model,
        "fw": d.fw,
        "online": d.online,
        "last_seen": d.last_seen.isoformat() if d.last_seen else None,
        "state": d.last_state,
        "themes": d.themes or DEFAULT_THEMES,
        "moods": d.moods,
        "preview": bool(d.preview_path),
        "features": d.features,
        "sounds": d.sounds if d.features["speaker"] else {},
        "groups": [g.pk for g in d.groups.all()],
    }


def _sound_catalog(devices):
    """Every sound any board knows (built-ins in their usual order, then clips), for the scene editor."""
    builtin, clips = [], set()
    for d in devices:
        if not d.features["speaker"]:
            continue
        for s in d.sounds.get("builtin") or []:
            if s not in builtin:
                builtin.append(s)
        clips.update(c["name"] for c in d.sounds.get("clips") or [] if isinstance(c, dict) and c.get("name"))
    if not builtin and any(d.features["speaker"] for d in devices):
        builtin = list(BUILTIN_SOUNDS)
    builtin.sort(key=lambda s: BUILTIN_SOUNDS.index(s) if s in BUILTIN_SOUNDS else len(BUILTIN_SOUNDS))
    return {"builtin": builtin, "clips": sorted(clips)}


def _group_json(g: Group):
    return {"id": g.pk, "name": g.name, "devices": [d.device_id for d in g.devices.all()]}


def _scene_json(s: Scene):
    return {
        "id": s.pk,
        "name": s.name,
        "group": s.group_id,
        "group_name": s.group.name if s.group else None,
        "state": s.state,
        "action": s.action,
    }


def _snapshot(**extra):
    """Everything the pages need for a first paint without a round trip."""
    devices = list(Device.objects.prefetch_related("groups"))
    groups = list(Group.objects.prefetch_related("devices"))
    return {
        "devices": [_device_json(d) for d in devices],
        "groups": [_group_json(g) for g in groups],
        "scenes": [_scene_json(s) for s in Scene.objects.select_related("group")],
        "themes": themes.catalog(devices),
        "moods": ["neutral", "angry", "surprised", "sleepy", "asleep"],
        "name_max": NAME_MAX,
        "sounds": _sound_catalog(devices),
        "sound_name_max": SOUND_NAME_MAX,
        "upload_accepts": audio.accepted_formats(),
        "upload_any_format": audio.ffmpeg_path() is not None,
        "voice_effects": [{"id": e, "name": audio.EFFECT_LABELS[e]} for e in VOICE_EFFECTS],
        "voice_clip": VOICE_CLIP,
        **extra,
    }


# --- Pages ---

def dashboard(request):
    snapshot = _snapshot()
    return render(request, "devices/dashboard.html", {
        "snapshot": snapshot,
        "boards": snapshot["devices"],
        "online": sum(1 for d in snapshot["devices"] if d["online"]),
    })


def board(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    snapshot = _snapshot(device=device.device_id)
    catalog = themes.catalog([device])
    return render(request, "devices/board.html", {
        "snapshot": snapshot,
        "device": device,
        "theme_groups": themes.grouped(catalog),
        "moods": device.moods,
        "page_title": device.name,
    })


def puppeteer(request):
    return render(request, "devices/puppeteer.html", {"snapshot": _snapshot(), "page_title": "Puppeteer"})


def gaze(request):
    return redirect("puppeteer", permanent=True)


def scenes(request):
    return render(request, "devices/scenes.html", {
        "snapshot": _snapshot(),
        "theme_groups": themes.grouped(themes.catalog(Device.objects.all())),
        "page_title": "Scenes & groups",
    })


# --- JSON API: boards ---

@require_http_methods(["GET", "POST"])
def api_devices(request):
    if request.method == "GET":
        devices = list(Device.objects.prefetch_related("groups"))
        if request.GET.get("refresh"):
            services.refresh(devices)
        return JsonResponse({"devices": [_device_json(d) for d in devices]})
    try:
        data = _body(request)
        host = str(data.get("host", "")).strip()
        port = int(data.get("port") or 80)
    except (ValidationError, ValueError) as err:
        return _error(str(err))
    if not host:
        return _error("host is required")
    if ":" in host and not data.get("port"):  # "10.0.0.5:8081"
        host, _, maybe_port = host.rpartition(":")
        if maybe_port.isdigit():
            port = int(maybe_port)
        else:
            host = f"{host}:{maybe_port}"
    try:
        device = services.register(host, port)
    except client.DeviceError as err:
        return _error(f"could not reach board: {err}", 502)
    return JsonResponse({"device": _device_json(device)}, status=201)


@require_http_methods(["GET", "PATCH", "DELETE"])
def api_device(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    if request.method == "DELETE":
        device.delete()
        return JsonResponse({"ok": True})
    if request.method == "GET":
        if request.GET.get("refresh"):
            services.refresh([device])
            device.refresh_from_db()
        return JsonResponse({"device": _device_json(device)})
    try:
        data = _body(request)
        name = clean_name(data.get("name"), "name", NAME_MAX)
    except ValidationError as err:
        return _error(str(err))
    result = services.rename(device, name)
    return JsonResponse({"device": _device_json(device), **result})


def api_device_preview(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    if not device.preview_path:
        return _error("this board has no live preview", 404)
    try:
        body, content_type = client.get_preview(device.host, device.port, device.preview_path)
    except client.DeviceError as err:
        return _error(str(err), 502)
    response = HttpResponse(body, content_type=content_type)
    response["Cache-Control"] = "no-store"
    return response


def api_device_state(request, device_id):
    """The board's state right now, straight through (for the live mic meter; nothing is stored)."""
    device = get_object_or_404(Device, device_id=device_id)
    try:
        state = client.get_state(device.host, device.port)
    except client.DeviceError as err:
        return _error(str(err), 502)
    return JsonResponse({"state": state})


# --- JSON API: sounds ---

def _sounds_ok(device):
    if not device.features["speaker"]:
        return _error("this board has no speaker (or its firmware predates sound)", 404)
    return None


@require_http_methods(["GET", "POST"])
def api_device_sounds(request, device_id):
    device = get_object_or_404(Device, device_id=device_id)
    if (bad := _sounds_ok(device)) is not None:
        return bad
    if request.method == "GET":
        try:
            listing = services.fetch_sounds(device)
        except client.DeviceError as err:
            return _error(str(err), 502)
        return JsonResponse({"sounds": listing})
    upload = next(iter(request.FILES.values()), None)
    if upload is None:
        return _error("choose an audio file to upload")
    if upload.size > settings.MAX_UPLOAD_BYTES:
        return _error(f"that file is too large to convert (limit {audio.human_size(settings.MAX_UPLOAD_BYTES)})")
    try:
        name = clean_sound_name(request.POST.get("name") or suggest_sound_name(upload.name))
    except ValidationError as err:
        return _error(str(err))
    if name in (device.sounds.get("builtin") or BUILTIN_SOUNDS):
        return _error(f"“{name}” is a built-in sound; pick another name")
    try:
        listing = services.upload_sound(device, name, upload.read(), upload.name or "")
    except services.UploadError as err:
        return _error(str(err))
    except client.DeviceError as err:
        return _error(str(err), 502)
    return JsonResponse({"sounds": listing, "name": name}, status=201)


@require_http_methods(["DELETE"])
def api_device_sound(request, device_id, name):
    device = get_object_or_404(Device, device_id=device_id)
    if (bad := _sounds_ok(device)) is not None:
        return bad
    try:
        name = clean_sound_name(name)
        listing = services.delete_sound(device, name)
    except ValidationError as err:
        return _error(str(err))
    except client.DeviceError as err:
        return _error(str(err), 502)
    return JsonResponse({"sounds": listing})


# --- JSON API: voice of the skull ---

def _voice_targets(raw_target):
    """Target T as JSON text (multipart) or an object; sound only goes to boards with a speaker."""
    if isinstance(raw_target, str):
        try:
            raw_target = json.loads(raw_target or "{}")
        except ValueError:
            raise ValidationError("target must be JSON") from None
    devices = services.resolve_targets(raw_target)
    if not devices:
        raise ValidationError("no boards to talk through")
    return devices


@require_POST
def api_voice(request):
    """Push-to-talk: multipart `file` (a recording), `effect`, `target` (JSON) -> converted, uploaded as the
    `voice` clip to every targeted speaker board and played at once."""
    upload = next(iter(request.FILES.values()), None)
    if upload is None:
        return _error("no recording was sent")
    if upload.size > settings.MAX_UPLOAD_BYTES:
        return _error(f"that recording is too large to convert (limit {audio.human_size(settings.MAX_UPLOAD_BYTES)})")
    try:
        effect = clean_effect(request.POST.get("effect"))
        devices = _voice_targets(request.POST.get("target"))
    except (ValidationError, services.TargetError) as err:
        return _error(str(err))
    if effect != "natural" and audio.ffmpeg_path() is None:
        return _error("voice effects need ffmpeg on the server; pick Natural or install ffmpeg")
    try:
        results = services.speak(devices, upload.read(), upload.name or "", effect)
    except services.UploadError as err:
        return _error(str(err))
    return JsonResponse({"results": results, "effect": effect})


@require_POST
def api_voice_url(request):
    """Play from URL: {"target": T, "url": "...", "effect": "demon"} -> downloaded here, converted, spoken."""
    try:
        data = _body(request)
        url = clean_url(data.get("url"))
        effect = clean_effect(data.get("effect"))
        devices = _voice_targets(data.get("target"))
    except (ValidationError, services.TargetError) as err:
        return _error(str(err))
    if effect != "natural" and audio.ffmpeg_path() is None:
        return _error("voice effects need ffmpeg on the server; pick Natural or install ffmpeg")
    try:
        results = services.speak_url(devices, url, effect)
    except services.UploadError as err:
        return _error(str(err))
    return JsonResponse({"results": results, "effect": effect})


@require_POST
def api_state(request):
    try:
        data = _body(request)
        state = clean_state(data.get("state"))
        devices = services.resolve_targets(data.get("target"))
    except (ValidationError, services.TargetError) as err:
        return _error(str(err))
    return JsonResponse({"results": services.apply_state(devices, state)})


@require_POST
def api_action(request):
    try:
        data = _body(request)
        payload = clean_action(data)
        devices = services.resolve_targets(data.get("target"))
    except (ValidationError, services.TargetError) as err:
        return _error(str(err))
    if payload["action"] in SOUND_ACTIONS and len(devices) > 1:
        # A group or "all": only boards with a speaker get sound actions; silent ones aren't failures.
        devices = [d for d in devices if d.features["speaker"]] or devices
    return JsonResponse({"results": services.apply_action(devices, payload)})


@require_POST
def api_scan(request):
    from .discovery import scan

    try:
        seconds = float(_body(request).get("seconds", 4))
    except (ValidationError, ValueError) as err:
        return _error(str(err))
    devices, errors = scan(min(max(seconds, 1), 15))
    return JsonResponse({"devices": [_device_json(d) for d in devices], "errors": errors})


def api_themes(request):
    return JsonResponse({"themes": themes.catalog(Device.objects.all())})


# --- JSON API: scenes ---

def _scene_fields(data):
    fields = clean_scene(data)
    if fields["group"] is not None:
        try:
            fields["group"] = Group.objects.get(pk=fields["group"])
        except Group.DoesNotExist:
            raise ValidationError("unknown group") from None
    return fields


@require_http_methods(["GET", "POST"])
def api_scenes(request):
    if request.method == "GET":
        return JsonResponse({"scenes": [_scene_json(s) for s in Scene.objects.select_related("group")]})
    try:
        fields = _scene_fields(_body(request))
        scene = Scene.objects.create(**fields)
    except ValidationError as err:
        return _error(str(err))
    except IntegrityError:
        return _error("a scene with that name already exists")
    return JsonResponse({"scene": _scene_json(scene)}, status=201)


@require_http_methods(["GET", "PUT", "DELETE"])
def api_scene(request, pk):
    scene = get_object_or_404(Scene.objects.select_related("group"), pk=pk)
    if request.method == "DELETE":
        scene.delete()
        return JsonResponse({"ok": True})
    if request.method == "PUT":
        try:
            for key, value in _scene_fields(_body(request)).items():
                setattr(scene, key, value)
            scene.save()
        except ValidationError as err:
            return _error(str(err))
        except IntegrityError:
            return _error("a scene with that name already exists")
    return JsonResponse({"scene": _scene_json(scene)})


@require_POST
def api_scene_apply(request, pk):
    scene = get_object_or_404(Scene, pk=pk)
    return JsonResponse({"scene": scene.name, "results": services.apply_scene(scene)})


# --- JSON API: groups ---

@require_http_methods(["GET", "POST"])
def api_groups(request):
    if request.method == "GET":
        return JsonResponse({"groups": [_group_json(g) for g in Group.objects.prefetch_related("devices")]})
    try:
        fields = clean_group(_body(request))
        group = Group.objects.create(name=fields["name"])
    except ValidationError as err:
        return _error(str(err))
    except IntegrityError:
        return _error("a group with that name already exists")
    group.devices.set(Device.objects.filter(device_id__in=fields["devices"]))
    return JsonResponse({"group": _group_json(group)}, status=201)


@require_http_methods(["GET", "PUT", "DELETE"])
def api_group(request, pk):
    group = get_object_or_404(Group, pk=pk)
    if request.method == "DELETE":
        group.delete()
        return JsonResponse({"ok": True})
    if request.method == "PUT":
        try:
            fields = clean_group(_body(request))
            group.name = fields["name"]
            group.save()
        except ValidationError as err:
            return _error(str(err))
        except IntegrityError:
            return _error("a group with that name already exists")
        group.devices.set(Device.objects.filter(device_id__in=fields["devices"]))
    return JsonResponse({"group": _group_json(group)})
