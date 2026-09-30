"""Base entity: one HA device per Spooky Eyes board."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SpookyEyesCoordinator


class SpookyEyesEntity(CoordinatorEntity[SpookyEyesCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SpookyEyesCoordinator, key: str) -> None:
        super().__init__(coordinator)
        info = coordinator.info
        device_id = coordinator.config_entry.unique_id or str(info.get("id"))
        self._attr_unique_id = f"{device_id}_{key}"
        connections = {(CONNECTION_NETWORK_MAC, info["mac"].lower())} if info.get("mac") else set()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            connections=connections,
            name=info.get("name") or coordinator.client.host,
            manufacturer=MANUFACTURER,
            model=info.get("model"),
            sw_version=info.get("fw"),
            configuration_url=coordinator.client.base_url,
        )

    @property
    def state_data(self) -> dict[str, Any]:
        return self.coordinator.data or {}
