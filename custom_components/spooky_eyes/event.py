"""Noise event: fires when the board's microphones hear something loud."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .const import has_feature
from .entity import SpookyEyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if has_feature(coordinator.info, "microphone"):
        async_add_entities([NoiseEvent(coordinator, "noise")])


class NoiseEvent(SpookyEyesEntity, EventEntity):
    _attr_translation_key = "noise"
    _attr_icon = "mdi:waveform"
    _attr_event_types = ["noise"]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.add_event_listener(self._on_board_event))

    @callback
    def _on_board_event(self, event: dict[str, Any]) -> None:
        if event.get("event") != "noise":
            return
        # direction: -1 = the board's left .. +1 = its right (from the two microphones).
        self._trigger_event("noise", {"direction": event.get("direction")})
        self.async_write_ha_state()
