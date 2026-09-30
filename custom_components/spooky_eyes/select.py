"""Theme and mood selects; options come from the board's /api/info."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .const import DEFAULT_MOODS
from .coordinator import SpookyEyesCoordinator
from .entity import SpookyEyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([ThemeSelect(coordinator), MoodSelect(coordinator)])


class ThemeSelect(SpookyEyesEntity, SelectEntity):
    _attr_translation_key = "theme"
    _attr_icon = "mdi:eye"

    def __init__(self, coordinator: SpookyEyesCoordinator) -> None:
        super().__init__(coordinator, "theme")
        themes = coordinator.info.get("themes") or []
        self._attr_options = [t["id"] for t in themes if t.get("id")]
        self._attr_extra_state_attributes = {
            "theme_names": {t["id"]: t.get("name", t["id"]) for t in themes if t.get("id")}
        }

    @property
    def current_option(self) -> str | None:
        theme = self.state_data.get("theme")
        return theme if theme in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_state(theme=option)


class MoodSelect(SpookyEyesEntity, SelectEntity):
    _attr_translation_key = "mood"
    _attr_icon = "mdi:emoticon-devil-outline"

    def __init__(self, coordinator: SpookyEyesCoordinator) -> None:
        super().__init__(coordinator, "mood")
        self._attr_options = list(coordinator.info.get("moods") or DEFAULT_MOODS)

    @property
    def current_option(self) -> str | None:
        mood = self.state_data.get("mood")
        return mood if mood in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_state(mood=option)
