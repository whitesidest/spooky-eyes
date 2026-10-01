import json

from django.db import IntegrityError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from . import client, services, themes
from .models import Device, Group, Scene
from .validation import NAME_MAX, ValidationError, clean_action, clean_group, clean_name, clean_scene, clean_state

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
        "groups": [g.pk for g in d.groups.all()],
    }


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
