"""Sensors: battery, sound level, and diagnostics (Wi-Fi signal, frame rate, uptime)."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .const import has_feature
from .coordinator import SpookyEyesCoordinator
from .entity import SpookyEyesEntity

SENSORS = (
    SensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="fps",
        translation_key="fps",
        native_unit_of_measurement="fps",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


# Optional hardware: (feature, description, path into the state object).
FEATURE_SENSORS = (
    (
        "battery",
        SensorEntityDescription(
            key="battery",
            device_class=SensorDeviceClass.BATTERY,
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        ("battery", "percent"),
    ),
    (
        "battery",
        SensorEntityDescription(
            key="battery_voltage",
            translation_key="battery_voltage",
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        ("battery", "voltage"),
    ),
    (
        "microphone",
        SensorEntityDescription(
            key="sound_level",
            translation_key="sound_level",
            device_class=SensorDeviceClass.SOUND_PRESSURE,
            native_unit_of_measurement="dB",
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        ("sound_level",),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [DiagnosticSensor(coordinator, d) for d in SENSORS]
    entities += [
        StateSensor(coordinator, d, path)
        for feature, d, path in FEATURE_SENSORS
        if has_feature(coordinator.info, feature)
    ]
    async_add_entities(entities)


class DiagnosticSensor(SpookyEyesEntity, SensorEntity):
    def __init__(self, coordinator: SpookyEyesCoordinator, description: SensorEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | None:
        return self.state_data.get(self.entity_description.key)


class StateSensor(SpookyEyesEntity, SensorEntity):
    """A value nested in the state object; None (unknown) when the board reports null."""

    def __init__(
        self, coordinator: SpookyEyesCoordinator, description: SensorEntityDescription, path: tuple[str, ...]
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._path = path

    @property
    def native_value(self) -> float | int | None:
        value = self.state_data
        for key in self._path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value
