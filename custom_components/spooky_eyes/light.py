"""The eyes themselves as a light: on/off blanks both panels, brightness drives the backlight."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .entity import SpookyEyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([SpookyEyesLight(entry.runtime_data, "eyes")])


class SpookyEyesLight(SpookyEyesEntity, LightEntity):
    _attr_name = None
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool | None:
        return self.state_data.get("on")

    @property
    def brightness(self) -> int | None:
        return self.state_data.get("brightness")

    async def async_turn_on(self, **kwargs: Any) -> None:
        changes: dict[str, Any] = {"on": True}
        if ATTR_BRIGHTNESS in kwargs:
            changes["brightness"] = int(kwargs[ATTR_BRIGHTNESS])
        await self.coordinator.async_set_state(**changes)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state(on=False)
