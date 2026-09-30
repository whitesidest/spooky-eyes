"""Pupil dilation override. Unknown while the board runs its own pupil ("auto")."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .entity import SpookyEyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([PupilNumber(entry.runtime_data, "pupil")])


class PupilNumber(SpookyEyesEntity, NumberEntity):
    _attr_translation_key = "pupil"
    _attr_icon = "mdi:circle-slice-8"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> float | None:
        pupil = self.state_data.get("pupil")
        return None if pupil is None else round(float(pupil) * 100, 1)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_state(pupil=round(value / 100, 3))
