"""Idle animation (autonomous saccades and blinks) and react-to-noise."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .const import has_feature
from .entity import SpookyEyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [IdleAnimationSwitch(coordinator, "autonomous")]
    if has_feature(coordinator.info, "microphone"):
        entities.append(ListenSwitch(coordinator, "listen"))
    async_add_entities(entities)


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


class ListenSwitch(SpookyEyesEntity, SwitchEntity):
    """Eyes jump and glance toward loud noises."""

    _attr_translation_key = "listen"
    _attr_icon = "mdi:ear-hearing"

    @property
    def is_on(self) -> bool | None:
        return self.state_data.get("listen")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state(listen=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state(listen=False)
