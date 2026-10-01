"""Spooky Eyes: animated eye boards controlled from Home Assistant."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service import async_extract_config_entry_ids
from homeassistant.helpers.typing import ConfigType

from .api import SpookyEyesApiError, SpookyEyesClient
from .const import (
    ACTION_LOOK,
    ACTION_SOUND,
    ACTION_STOP_SOUND,
    ACTIONS,
    ATTR_ACTION,
    ATTR_SOUND,
    ATTR_DURATION,
    ATTR_X,
    ATTR_Y,
    DEFAULT_PORT,
    DOMAIN,
    PLATFORMS,
    SERVICE_ACTION,
    SERVICE_LOOK,
    SERVICE_PLAY_SOUND,
    SERVICE_STOP_SOUND,
)
from .coordinator import SpookyEyesCoordinator

_LOGGER = logging.getLogger(__name__)

type SpookyEyesConfigEntry = ConfigEntry[SpookyEyesCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_GAZE = vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0))
_DURATION = vol.All(vol.Coerce(float), vol.Range(min=0, max=3600))

LOOK_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_X): _GAZE,
        vol.Required(ATTR_Y): _GAZE,
        vol.Optional(ATTR_DURATION, default=0): _DURATION,
    }
)
ACTION_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_ACTION): vol.In(ACTIONS),
        vol.Optional(ATTR_X): _GAZE,
        vol.Optional(ATTR_Y): _GAZE,
        vol.Optional(ATTR_DURATION): _DURATION,
    }
)

_SOUND_NAME = vol.All(cv.string, vol.Length(min=1, max=24))
PLAY_SOUND_SCHEMA = cv.make_entity_service_schema({vol.Required(ATTR_SOUND): _SOUND_NAME})
STOP_SOUND_SCHEMA = cv.make_entity_service_schema({})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def _fan_out(call: ServiceCall, action: str, params: dict) -> None:
        entry_ids = await async_extract_config_entry_ids(call)
        coordinators = [
            entry.runtime_data
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            if entry.entry_id in entry_ids
        ]
        if not coordinators:
            raise ServiceValidationError("No Spooky Eyes devices matched the target.")
        results = await asyncio.gather(
            *(c.async_action(action, **params) for c in coordinators), return_exceptions=True
        )
        failed = [
            f"{c.info.get('name') or c.client.host}: {r}"
            for c, r in zip(coordinators, results)
            if isinstance(r, Exception)
        ]
        if failed:
            raise HomeAssistantError("Spooky Eyes action failed on " + "; ".join(failed))

    async def _look(call: ServiceCall) -> None:
        await _fan_out(
            call,
            ACTION_LOOK,
            {k: call.data[k] for k in (ATTR_X, ATTR_Y, ATTR_DURATION)},
        )

    async def _action(call: ServiceCall) -> None:
        params = {k: call.data[k] for k in (ATTR_X, ATTR_Y, ATTR_DURATION) if k in call.data}
        await _fan_out(call, call.data[ATTR_ACTION], params)

    async def _play_sound(call: ServiceCall) -> None:
        await _fan_out(call, ACTION_SOUND, {"name": call.data[ATTR_SOUND]})

    async def _stop_sound(call: ServiceCall) -> None:
        await _fan_out(call, ACTION_STOP_SOUND, {})

    hass.services.async_register(DOMAIN, SERVICE_LOOK, _look, schema=LOOK_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_PLAY_SOUND, _play_sound, schema=PLAY_SOUND_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP_SOUND, _stop_sound, schema=STOP_SOUND_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ACTION, _action, schema=ACTION_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SpookyEyesConfigEntry) -> bool:
    client = SpookyEyesClient(
        async_get_clientsession(hass), entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT)
    )
    try:
        info = await client.get_info()
    except SpookyEyesApiError as err:
        raise ConfigEntryNotReady(f"Spooky Eyes at {client.host} unreachable: {err}") from err

    coordinator = SpookyEyesCoordinator(hass, entry, client, info)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start_push()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SpookyEyesConfigEntry) -> bool:
    await entry.runtime_data.stop_push()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
