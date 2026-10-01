"""Speaker, microphone and battery entities/services for boards that report those features."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.spooky_eyes.const import DOMAIN, EVENT_NOISE

from .conftest import BASE, DEVICE_ID, HOST, INFO, STATE, mock_board, posts_to

AUDIO_INFO = {**INFO, "features": {"speaker": True, "microphone": True, "battery": True}}
AUDIO_STATE = {
    **STATE,
    "volume": 70,
    "listen": False,
    "sensitivity": 50,
    "sound_level": -42,
    "playing": None,
    "battery": {"voltage": 3.91, "percent": 67},
}


@pytest.fixture
async def audio_entry(hass, aioclient_mock):
    mock_board(aioclient_mock, info=AUDIO_INFO, state=AUDIO_STATE)
    entry = MockConfigEntry(domain=DOMAIN, data={"host": HOST, "port": 80}, unique_id=DEVICE_ID, title="Porch Eyes")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_audio_entities(hass, audio_entry):
    assert hass.states.get("number.porch_eyes_volume").state == "70"
    assert hass.states.get("number.porch_eyes_noise_sensitivity").state == "50"
    assert hass.states.get("switch.porch_eyes_react_to_noise").state == "off"
    assert hass.states.get("sensor.porch_eyes_battery").state == "67"
    assert hass.states.get("sensor.porch_eyes_battery_voltage").state == "3.91"
    assert hass.states.get("sensor.porch_eyes_sound_level").state == "-42"
    assert hass.states.get("button.porch_eyes_stop_sound") is not None
    assert hass.states.get("event.porch_eyes_noise") is not None


async def test_old_firmware_has_no_audio_entities(hass, configured_entry):
    for entity_id in ("number.porch_eyes_volume", "switch.porch_eyes_react_to_noise",
                      "sensor.porch_eyes_battery", "event.porch_eyes_noise", "button.porch_eyes_stop_sound"):
        assert hass.states.get(entity_id) is None, entity_id


async def test_battery_unknown_without_cell(hass, audio_entry):
    audio_entry.runtime_data.async_set_updated_data({**AUDIO_STATE, "battery": None})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.porch_eyes_battery").state == "unknown"


async def test_volume_listen_and_stop(hass, audio_entry, aioclient_mock):
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.porch_eyes_volume", "value": 35}, blocking=True
    )
    assert posts_to(aioclient_mock, "/api/state")[-1][2] == {"volume": 35}
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.porch_eyes_react_to_noise"}, blocking=True
    )
    assert posts_to(aioclient_mock, "/api/state")[-1][2] == {"listen": True}
    await hass.services.async_call("button", "press", {"entity_id": "button.porch_eyes_stop_sound"}, blocking=True)
    assert posts_to(aioclient_mock, "/api/action")[-1][2] == {"action": "stop_sound"}


async def test_play_and_stop_sound_services(hass, audio_entry, aioclient_mock):
    await hass.services.async_call(
        DOMAIN, "play_sound", {"entity_id": "light.porch_eyes", "sound": "growl"}, blocking=True
    )
    assert posts_to(aioclient_mock, "/api/action")[-1][2] == {"action": "sound", "name": "growl"}
    await hass.services.async_call(DOMAIN, "stop_sound", {"entity_id": "light.porch_eyes"}, blocking=True)
    assert posts_to(aioclient_mock, "/api/action")[-1][2] == {"action": "stop_sound"}


async def test_noise_event_fires_entity_and_bus(hass, audio_entry):
    bus_events = async_capture_events(hass, EVENT_NOISE)
    audio_entry.runtime_data._handle_event({"type": "event", "event": "noise", "direction": -0.6})
    await hass.async_block_till_done()
    state = hass.states.get("event.porch_eyes_noise")
    assert state.attributes["event_type"] == "noise"
    assert state.attributes["direction"] == -0.6
    assert bus_events and bus_events[0].data == {"board_id": DEVICE_ID, "name": "Porch Eyes", "direction": -0.6}


async def test_ws_dispatch_routes_events():
    from custom_components.spooky_eyes.api import SpookyEyesClient

    states, events = [], []
    SpookyEyesClient._dispatch('{"type":"event","event":"noise","direction":0.4}', states.append, events.append)
    SpookyEyesClient._dispatch('{"type":"state","state":{"on":true}}', states.append, events.append)
    assert events == [{"type": "event", "event": "noise", "direction": 0.4}]
    assert states == [{"on": True}]
