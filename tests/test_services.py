from __future__ import annotations

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spooky_eyes.const import DOMAIN

from .conftest import INFO, mock_board, posts_to

HOST2 = "192.168.1.51"
BASE2 = f"http://{HOST2}"
INFO2 = {**INFO, "id": "0f0f0f0f0f0f", "name": "Window Eyes", "mac": "0F:0F:0F:0F:0F:0F", "ip": HOST2}


@pytest.fixture
async def two_boards(hass, configured_entry, aioclient_mock):
    mock_board(aioclient_mock, base=BASE2, info=INFO2)
    entry2 = MockConfigEntry(
        domain=DOMAIN, data={"host": HOST2, "port": 80}, unique_id=INFO2["id"], entry_id="entry2"
    )
    entry2.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry2.entry_id)
    await hass.async_block_till_done()
    return configured_entry, entry2


def _device_id(hass, unique):
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, unique)}).id


def _actions(aioclient_mock, base):
    return [c[2] for c in posts_to(aioclient_mock, "/api/action") if str(c[1]).startswith(base)]


async def test_look_fans_out_to_devices(hass, two_boards, aioclient_mock):
    await hass.services.async_call(
        DOMAIN,
        "look",
        {"device_id": [_device_id(hass, INFO["id"]), _device_id(hass, INFO2["id"])], "x": -0.5, "y": 0.25, "duration": 3},
        blocking=True,
    )
    expected = {"action": "look", "x": -0.5, "y": 0.25, "duration": 3.0}
    assert _actions(aioclient_mock, "http://192.168.1.50") == [expected]
    assert _actions(aioclient_mock, BASE2) == [expected]


async def test_action_by_entity_targets_one_board(hass, two_boards, aioclient_mock):
    await hass.services.async_call(
        DOMAIN, "action", {"entity_id": "select.window_eyes_theme", "action": "startle"}, blocking=True
    )
    assert _actions(aioclient_mock, BASE2) == [{"action": "startle"}]
    assert _actions(aioclient_mock, "http://192.168.1.50") == []


async def test_action_failure_names_board(hass, configured_entry, aioclient_mock):
    aioclient_mock.clear_requests()
    aioclient_mock.post("http://192.168.1.50/api/action", status=500)
    with pytest.raises(HomeAssistantError, match="Porch Eyes"):
        await hass.services.async_call(
            DOMAIN, "action", {"device_id": _device_id(hass, INFO["id"]), "action": "blink"}, blocking=True
        )
