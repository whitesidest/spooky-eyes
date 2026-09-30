"""Keeps one board's state: pushed over WebSocket, polled as a fallback."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SpookyEyesApiError, SpookyEyesClient
from .const import DOMAIN, FALLBACK_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SpookyEyesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SpookyEyesClient,
        info: dict[str, Any],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {info.get('name') or client.host}",
            update_interval=FALLBACK_SCAN_INTERVAL,
        )
        self.client = client
        self.info = info
        self.ws_connected = False

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.get_state()
        except SpookyEyesApiError as err:
            raise UpdateFailed(str(err)) from err

    def start_push(self) -> None:
        self.client.start_listener(self._handle_push, self._handle_connection)

    async def stop_push(self) -> None:
        await self.client.stop_listener()

    @callback
    def _handle_push(self, state: dict[str, Any]) -> None:
        # A push may be partial; merge onto what we know.
        self.async_set_updated_data({**(self.data or {}), **state})

    @callback
    def _handle_connection(self, connected: bool) -> None:
        was, self.ws_connected = self.ws_connected, connected
        if connected and not was:
            # Resync after (re)connect in case pushes were missed while down.
            self.hass.async_create_task(self.async_request_refresh())

    async def async_set_state(self, **changes: Any) -> None:
        """Send a partial state and apply the board's reply immediately."""
        try:
            state = await self.client.set_state(**changes)
        except SpookyEyesApiError as err:
            raise HomeAssistantError(f"Spooky Eyes {self.client.host}: {err}") from err
        if isinstance(state, dict):
            self.async_set_updated_data({**(self.data or {}), **state})

    async def async_action(self, action: str, **params: Any) -> None:
        try:
            await self.client.action(action, **params)
        except SpookyEyesApiError as err:
            raise HomeAssistantError(f"Spooky Eyes {self.client.host}: {err}") from err
