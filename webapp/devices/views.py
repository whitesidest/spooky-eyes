import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods, require_POST

from . import client, services
from .models import Device, Group, Scene
from .validation import ValidationError, clean_action, clean_state

DEFAULT_THEMES = [
    {"id": t, "name": t.title()} for t in ("human", "cat", "fire", "alien", "sauron", "terminator")
]


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
        "host": d.host,
        "port": d.port,
        "model": d.model,
        "fw": d.fw,
        "online": d.online,
        "state": d.last_state,
        "themes": d.themes or DEFAULT_THEMES,
        "moods": d.moods,
    }


# --- Pages ---

def dashboard(request):
    return render(request, "devices/dashboard.html", {
        "devices": Device.objects.all(),
        "groups": Group.objects.all(),
        "scenes": Scene.objects.all(),
        "default_themes": DEFAULT_THEMES,
    })


def gaze(request):
    return render(request, "devices/gaze.html", {
        "devices": Device.objects.all(),
        "groups": Group.objects.all(),
    })


# --- JSON API ---

@require_http_methods(["GET", "POST"])
def api_devices(request):
    if request.method == "GET":
        devices = list(Device.objects.all())
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
    try:
        device = services.register(host, port)
    except client.DeviceError as err:
        return _error(f"could not reach board: {err}", 502)
    return JsonResponse({"device": _device_json(device)}, status=201)


@require_http_methods(["DELETE"])
def api_device_delete(request, device_id):
    get_object_or_404(Device, device_id=device_id).delete()
    return JsonResponse({"ok": True})


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
    return JsonResponse({"results": services.apply_action(devices, payload)})


@require_POST
def api_scene_apply(request, pk):
    scene = get_object_or_404(Scene, pk=pk)
    return JsonResponse({"scene": scene.name, "results": services.apply_scene(scene)})


@require_POST
def api_scan(request):
    from .discovery import scan

    try:
        seconds = float(_body(request).get("seconds", 4))
    except (ValidationError, ValueError) as err:
        return _error(str(err))
    devices, errors = scan(min(max(seconds, 1), 15))
    return JsonResponse({"devices": [_device_json(d) for d in devices], "errors": errors})
