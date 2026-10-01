"""Theme, mood and startle-sound selects; options come from the board."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .api import SpookyEyesApiError
from .const import DEFAULT_MOODS, THEME_SOUND_NONE
from .coordinator import SpookyEyesCoordinator
from .entity import SpookyEyesEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = [ThemeSelect(coordinator), MoodSelect(coordinator)]
    if coordinator.has_speaker and coordinator.has_theme_sounds:
        if not coordinator.sounds:
            try:
                await coordinator.async_fetch_sounds(notify=False)
            except SpookyEyesApiError:
                pass
        entities.append(ThemeSoundSelect(coordinator))
    async_add_entities(entities)


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


class ThemeSoundSelect(SpookyEyesEntity, SelectEntity):
    """The sound paired with the current theme, played whenever the eyes startle.

    Options are "none" plus every built-in and uploaded clip; they follow the board's sound
    listing, which the coordinator refreshes on its fallback poll and whenever the board reports
    a pairing the list doesn't know yet.
    """

    _attr_translation_key = "theme_sound"
    _attr_icon = "mdi:music-box"

    def __init__(self, coordinator: SpookyEyesCoordinator) -> None:
        super().__init__(coordinator, "theme_sound")
        self._attr_options = self._build_options()

    def _build_options(self) -> list[str]:
        return [THEME_SOUND_NONE] + [n for n in self.coordinator.sound_names if n != THEME_SOUND_NONE]

    @property
    def current_option(self) -> str | None:
        sound = self.state_data.get("theme_sound")
        return THEME_SOUND_NONE if not sound else (sound if sound in self.options else None)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        theme = self.state_data.get("theme")
        default = next((t.get("sound") for t in self.coordinator.info.get("themes") or [] if t.get("id") == theme), None)
        return {"theme": theme, "default_sound": default}

    @callback
    def _handle_coordinator_update(self) -> None:
        options = self._build_options()
        if options != self._attr_options:
            self._attr_options = options
        sound = self.state_data.get("theme_sound")
        if sound and sound not in options:
            # A clip we haven't seen (uploaded elsewhere): re-read the board's listing.
            self.hass.async_create_task(self._refresh_sounds())
        super()._handle_coordinator_update()

    async def _refresh_sounds(self) -> None:
        try:
            await self.coordinator.async_fetch_sounds()
        except SpookyEyesApiError:
            pass

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_state(theme_sound=None if option == THEME_SOUND_NONE else option)
