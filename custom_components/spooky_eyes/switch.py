"""Idle animation: autonomous saccades and blinks."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .entity import SpookyEyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([IdleAnimationSwitch(entry.runtime_data, "autonomous")])


class IdleAnimationSwitch(SpookyEyesEntity, SwitchEntity):
    _attr_translation_key = "autonomous"
    _attr_icon = "mdi:eye-refresh-outline"

    @property
    def is_on(self) -> bool | None:
        return self.state_data.get("autonomous")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state(autonomous=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state(autonomous=False)
