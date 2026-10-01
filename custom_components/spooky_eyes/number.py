"""Pupil dilation override, speaker volume and noise sensitivity."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .const import has_feature
from .entity import SpookyEyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = [PupilNumber(coordinator, "pupil")]
    if has_feature(coordinator.info, "speaker"):
        entities.append(PercentNumber(coordinator, "volume", "mdi:volume-high"))
    if has_feature(coordinator.info, "microphone"):
        entities.append(PercentNumber(coordinator, "sensitivity", "mdi:ear-hearing"))
    async_add_entities(entities)


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


class PercentNumber(SpookyEyesEntity, NumberEntity):
    """A 0-100 board setting stored under the same key in the state object."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, key: str, icon: str) -> None:
        super().__init__(coordinator, key)
        self._key = key
        self._attr_translation_key = key
        self._attr_icon = icon

    @property
    def native_value(self) -> float | None:
        return self.state_data.get(self._key)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_state(**{self._key: int(value)})
