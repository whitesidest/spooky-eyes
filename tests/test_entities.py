from __future__ import annotations

import pytest
from homeassistant.helpers import entity_registry as er

from .conftest import STATE, posts_to

LIGHT = "light.porch_eyes"
THEME = "select.porch_eyes_theme"
MOOD = "select.porch_eyes_mood"
IDLE = "switch.porch_eyes_idle_animation"
PUPIL = "number.porch_eyes_pupil_dilation"


def _last_json(board, suffix):
    calls = posts_to(board, suffix)
    assert calls, f"no POST to {suffix}"
    return calls[-1][2]


async def test_entities_reflect_state(hass, configured_entry):
    light = hass.states.get(LIGHT)
    assert light.state == "on"
    assert light.attributes["brightness"] == 200
    theme = hass.states.get(THEME)
    assert theme.state == "sauron"
    assert theme.attributes["options"] == ["human", "cat", "sauron", "terminator"]
    assert hass.states.get(MOOD).state == "neutral"
    assert hass.states.get(IDLE).state == "on"
    assert hass.states.get(PUPIL).state == "unknown"
    assert hass.states.get("sensor.porch_eyes_wi_fi_signal").state == "-58"
    assert hass.states.get("sensor.porch_eyes_frame_rate").state == "31.5"
    for key in ("blink", "wink_left", "wink_right", "startle", "eye_roll", "auto_pupil"):
        assert hass.states.get(f"button.porch_eyes_{key}") is not None, key


async def test_device_registered_with_mac(hass, configured_entry):
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get_device(identifiers={("spooky_eyes", "a1b2c3d4e5f6")})
    assert device.name == "Porch Eyes"
    assert device.sw_version == "0.1.0"
    assert (dr.CONNECTION_NETWORK_MAC, "a1:b2:c3:d4:e5:f6") in device.connections


@pytest.mark.parametrize(
    ("domain", "service", "data", "expected"),
    [
        ("light", "turn_off", {"entity_id": LIGHT}, {"on": False}),
        ("light", "turn_on", {"entity_id": LIGHT, "brightness": 80}, {"on": True, "brightness": 80}),
        ("select", "select_option", {"entity_id": THEME, "option": "cat"}, {"theme": "cat"}),
        ("select", "select_option", {"entity_id": MOOD, "option": "angry"}, {"mood": "angry"}),
        ("switch", "turn_off", {"entity_id": IDLE}, {"autonomous": False}),
        ("number", "set_value", {"entity_id": PUPIL, "value": 75}, {"pupil": 0.75}),
        ("button", "press", {"entity_id": "button.porch_eyes_auto_pupil"}, {"pupil": None}),
    ],
)
async def test_state_commands(hass, configured_entry, board, domain, service, data, expected):
    await hass.services.async_call(domain, service, data, blocking=True)
    assert _last_json(board, "/api/state") == expected


@pytest.mark.parametrize(
    ("button", "action"),
    [
        ("blink", "blink"),
        ("wink_left", "wink_left"),
        ("wink_right", "wink_right"),
        ("startle", "startle"),
        ("eye_roll", "roll"),
    ],
)
async def test_action_buttons(hass, configured_entry, board, button, action):
    await hass.services.async_call("button", "press", {"entity_id": f"button.porch_eyes_{button}"}, blocking=True)
    assert _last_json(board, "/api/action") == {"action": action}


async def test_push_updates_entities(hass, configured_entry):
    coordinator = configured_entry.runtime_data
    coordinator._handle_push({**STATE, "theme": "terminator", "pupil": 0.4, "on": False})
    await hass.async_block_till_done()
    assert hass.states.get(THEME).state == "terminator"
    assert hass.states.get(PUPIL).state == "40.0"
    assert hass.states.get(LIGHT).state == "off"


async def test_unique_ids(hass, configured_entry):
    reg = er.async_get(hass)
    assert reg.async_get(THEME).unique_id == "a1b2c3d4e5f6_theme"


async def test_ws_dispatch_filters_messages():
    from custom_components.spooky_eyes.api import SpookyEyesClient

    got = []
    SpookyEyesClient._dispatch('{"type":"state","state":{"theme":"cat"}}', got.append)
    SpookyEyesClient._dispatch('{"type":"log","msg":"hi"}', got.append)
    SpookyEyesClient._dispatch("not json", got.append)
    assert got == [{"theme": "cat"}]


async def test_unload_stops_listener(hass, configured_entry):
    assert await hass.config_entries.async_unload(configured_entry.entry_id)
    await hass.async_block_till_done()
    assert configured_entry.state.value == "not_loaded"
