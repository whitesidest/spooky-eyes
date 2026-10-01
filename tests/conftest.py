"""Shared fixtures: a configured Spooky Eyes board backed by canned REST responses.

`aioclient_mock` intercepts the aiohttp session HA hands the integration. The
WebSocket listener is stubbed out; tests push state through the coordinator.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spooky_eyes.const import DOMAIN

HOST = "192.168.1.50"
BASE = f"http://{HOST}"
DEVICE_ID = "a1b2c3d4e5f6"

INFO = {
    "id": DEVICE_ID,
    "name": "Porch Eyes",
    "model": "dualeye-1.28",
    "fw": "0.1.0",
    "mac": "A1:B2:C3:D4:E5:F6",
    "ip": HOST,
    "themes": [
        {"id": "human", "name": "Human"},
        {"id": "cat", "name": "Cat"},
        {"id": "sauron", "name": "Sauron"},
        {"id": "terminator", "name": "Terminator"},
    ],
    "moods": ["neutral", "angry", "surprised", "sleepy", "asleep"],
    "eyes": 2,
}

STATE = {
    "on": True,
    "brightness": 200,
    "theme": "sauron",
    "mood": "neutral",
    "autonomous": True,
    "pupil": None,
    "gaze": {"x": 0.0, "y": 0.0},
    "rssi": -58,
    "fps": 31.5,
    "uptime": 1234,
}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def _no_websocket():
    with patch("custom_components.spooky_eyes.api.SpookyEyesClient.start_listener"):
        yield


SOUNDS = {
    "builtin": ["growl", "heartbeat", "whisper", "creak", "zap", "chime", "test"],
    "clips": [{"name": "boo", "bytes": 64044}],
    "free_bytes": 3_000_000,
}


def mock_board(
    aioclient_mock, base: str = BASE, info: dict | None = None, state: dict | None = None, sounds: dict | None = None
):
    aioclient_mock.get(f"{base}/api/info", json=info or INFO)
    aioclient_mock.get(f"{base}/api/state", json=state or STATE)
    aioclient_mock.post(f"{base}/api/state", json=state or STATE)
    aioclient_mock.post(f"{base}/api/action", json={"ok": True})
    aioclient_mock.get(f"{base}/api/sounds", json=sounds or SOUNDS)
    aioclient_mock.post(f"{base}/api/sounds", json=sounds or SOUNDS)


@pytest.fixture
def board(aioclient_mock):
    mock_board(aioclient_mock)
    return aioclient_mock


@pytest.fixture
async def configured_entry(hass, board):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": HOST, "port": 80},
        unique_id=DEVICE_ID,
        title="Porch Eyes",
        entry_id="entry1",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def posts_to(aioclient_mock, path_suffix: str) -> list:
    return [c for c in aioclient_mock.mock_calls if c[0] == "POST" and str(c[1]).endswith(path_suffix)]
