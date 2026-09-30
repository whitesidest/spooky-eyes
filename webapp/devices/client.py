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


def get_info(host: str, port: int = 80) -> dict:
    return _request("GET", host, port, "/api/info")


def get_state(host: str, port: int = 80) -> dict:
    return _request("GET", host, port, "/api/state")


def set_state(host: str, port: int, state: dict) -> dict:
    """Send a partial state; the device answers with its full state."""
    return _request("POST", host, port, "/api/state", json=state)


def action(host: str, port: int, payload: dict) -> dict:
    return _request("POST", host, port, "/api/action", json=payload)
