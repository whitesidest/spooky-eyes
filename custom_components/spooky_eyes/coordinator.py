"""Keeps one board's state: pushed over WebSocket, polled as a fallback."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SpookyEyesApiError, SpookyEyesClient
from .const import BUILTIN_SOUNDS, DOMAIN, EVENT_NOISE, FALLBACK_SCAN_INTERVAL, has_feature

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
        self._event_listeners: list[Callable[[dict[str, Any]], None]] = []
        # Last /api/sounds listing (speaker boards): built-ins, uploaded clips, free space.
        self.sounds: dict[str, Any] = {}

    @property
    def has_speaker(self) -> bool:
        return has_feature(self.info, "speaker")

    @property
    def has_theme_sounds(self) -> bool:
        """Firmware that pairs a sound with each theme reports `theme_sounds` in its state."""
        return "theme_sounds" in (self.data or {})

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            state = await self.client.get_state()
        except SpookyEyesApiError as err:
            raise UpdateFailed(str(err)) from err
        if self.has_speaker:
            # Clips come and go via the web controller; the fallback poll keeps the lists fresh.
            try:
                await self.async_fetch_sounds(notify=False)
            except SpookyEyesApiError as err:
                _LOGGER.debug("Spooky Eyes %s: sound listing failed: %s", self.client.host, err)
        return state

    async def async_fetch_sounds(self, notify: bool = True) -> dict[str, Any]:
        """GET /api/sounds and remember it; entities re-read it on the next update."""
        self.set_sounds(await self.client.get_sounds(), notify=notify)
        return self.sounds

    @callback
    def set_sounds(self, listing: dict[str, Any], notify: bool = True) -> None:
        builtin = [s for s in listing.get("builtin") or [] if isinstance(s, str)] or list(BUILTIN_SOUNDS)
        clips = [c for c in listing.get("clips") or [] if isinstance(c, dict) and isinstance(c.get("name"), str)]
        self.sounds = {"builtin": builtin, "clips": clips, "free_bytes": int(listing.get("free_bytes") or 0)}
        if notify:
            self.async_update_listeners()

    @property
    def sound_names(self) -> list[str]:
        """Every playable name: built-ins (board order) then clips (alphabetical)."""
        builtin = self.sounds.get("builtin") or list(BUILTIN_SOUNDS)
        return builtin + sorted(c["name"] for c in self.sounds.get("clips") or [])

    def start_push(self) -> None:
        self.client.start_listener(self._handle_push, self._handle_connection, self._handle_event)

    async def stop_push(self) -> None:
        await self.client.stop_listener()

    @callback
    def _handle_push(self, state: dict[str, Any]) -> None:
        # A push may be partial; merge onto what we know.
        self.async_set_updated_data({**(self.data or {}), **state})

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        """Board events (e.g. a loud noise): fire on the HA bus and notify event entities."""
        if event.get("event") == "noise":
            self.hass.bus.async_fire(
                EVENT_NOISE,
                {
                    "board_id": self.info.get("id"),
                    "name": self.info.get("name"),
                    "direction": event.get("direction"),
                },
            )
        for listener in list(self._event_listeners):
            listener(event)

    @callback
    def add_event_listener(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self._event_listeners.append(listener)
        return lambda: self._event_listeners.remove(listener)

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
