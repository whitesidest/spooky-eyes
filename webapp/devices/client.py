"""HTTP client for the Spooky Eyes device API (see PLAN.md). Kept isolated so tests can mock it."""
from __future__ import annotations

import httpx
from django.conf import settings


class DeviceError(Exception):
    pass


def _url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


def _request(method: str, host: str, port: int, path: str, json=None) -> dict:
    try:
        resp = httpx.request(method, _url(host, port, path), json=json, timeout=settings.DEVICE_TIMEOUT)
    except httpx.HTTPError as err:
        raise DeviceError(f"{host}: {err.__class__.__name__}") from err
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", resp.text)
        except ValueError:
            detail = resp.text
        raise DeviceError(f"{host}: HTTP {resp.status_code} {detail}".strip())
    try:
        return resp.json()
    except ValueError as err:
        raise DeviceError(f"{host}: invalid JSON") from err


def get_preview(host: str, port: int, path: str) -> tuple[bytes, str]:
    """Fetch a board's live picture; returns (body, content type)."""
    try:
        resp = httpx.get(_url(host, port, path), timeout=settings.DEVICE_TIMEOUT)
    except httpx.HTTPError as err:
        raise DeviceError(f"{host}: {err.__class__.__name__}") from err
    if resp.status_code >= 400:
        raise DeviceError(f"{host}: HTTP {resp.status_code}")
    return resp.content, resp.headers.get("content-type", "application/octet-stream")


def get_info(host: str, port: int = 80) -> dict:
    return _request("GET", host, port, "/api/info")


def get_state(host: str, port: int = 80) -> dict:
    return _request("GET", host, port, "/api/state")


def set_state(host: str, port: int, state: dict) -> dict:
    """Send a partial state; the device answers with its full state."""
    return _request("POST", host, port, "/api/state", json=state)


def action(host: str, port: int, payload: dict) -> dict:
    return _request("POST", host, port, "/api/action", json=payload)


# --- Sounds (firmware with features.speaker) ---

def get_sounds(host: str, port: int) -> dict:
    """{"builtin": [...], "clips": [{"name", "bytes"}], "free_bytes": N}"""
    return _request("GET", host, port, "/api/sounds")


def upload_sound(host: str, port: int, name: str, wav: bytes) -> dict:
    """Multipart upload of a 16-bit PCM WAV; the board answers with its sound listing."""
    url = _url(host, port, f"/api/sounds?name={name}")
    timeout = max(settings.DEVICE_TIMEOUT, settings.DEVICE_UPLOAD_TIMEOUT)
    try:
        resp = httpx.post(url, files={"file": (f"{name}.wav", wav, "audio/wav")}, timeout=timeout)
    except httpx.HTTPError as err:
        raise DeviceError(f"{host}: {err.__class__.__name__}") from err
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", resp.text)
        except ValueError:
            detail = resp.text
        raise DeviceError(f"{host}: HTTP {resp.status_code} {detail}".strip())
    try:
        return resp.json()
    except ValueError as err:
        raise DeviceError(f"{host}: invalid JSON") from err


def delete_sound(host: str, port: int, name: str) -> dict:
    return _request("DELETE", host, port, f"/api/sounds?name={name}")
