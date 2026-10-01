"""One-shot animations, plus handing pupil control back to the board."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .const import (
    ACTION_BLINK,
    ACTION_ROLL,
    ACTION_STARTLE,
    ACTION_STOP_SOUND,
    ACTION_WINK_LEFT,
    ACTION_WINK_RIGHT,
    has_feature,
)
from .coordinator import SpookyEyesCoordinator
from .entity import SpookyEyesEntity


@dataclass(frozen=True, kw_only=True)
class SpookyEyesButtonDescription(ButtonEntityDescription):
    action: str | None = None  # None = not an action; see AutoPupilButton


ACTION_BUTTONS = (
    SpookyEyesButtonDescription(key=ACTION_BLINK, translation_key=ACTION_BLINK, icon="mdi:eye-closed", action=ACTION_BLINK),
    SpookyEyesButtonDescription(key=ACTION_WINK_LEFT, translation_key=ACTION_WINK_LEFT, icon="mdi:eye-arrow-left-outline", action=ACTION_WINK_LEFT),
    SpookyEyesButtonDescription(key=ACTION_WINK_RIGHT, translation_key=ACTION_WINK_RIGHT, icon="mdi:eye-arrow-right-outline", action=ACTION_WINK_RIGHT),
    SpookyEyesButtonDescription(key=ACTION_STARTLE, translation_key=ACTION_STARTLE, icon="mdi:eye-plus", action=ACTION_STARTLE),
    SpookyEyesButtonDescription(key=ACTION_ROLL, translation_key=ACTION_ROLL, icon="mdi:rotate-right", action=ACTION_ROLL),
)

STOP_SOUND_BUTTON = SpookyEyesButtonDescription(
    key=ACTION_STOP_SOUND, translation_key=ACTION_STOP_SOUND, icon="mdi:volume-off", action=ACTION_STOP_SOUND
)


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    buttons = [ActionButton(coordinator, d) for d in ACTION_BUTTONS] + [AutoPupilButton(coordinator, "auto_pupil")]
    if has_feature(coordinator.info, "speaker"):
        buttons.append(ActionButton(coordinator, STOP_SOUND_BUTTON))
    async_add_entities(buttons)


class ActionButton(SpookyEyesEntity, ButtonEntity):
    entity_description: SpookyEyesButtonDescription

    def __init__(self, coordinator: SpookyEyesCoordinator, description: SpookyEyesButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.coordinator.async_action(self.entity_description.action)


class AutoPupilButton(SpookyEyesEntity, ButtonEntity):
    _attr_translation_key = "auto_pupil"
    _attr_icon = "mdi:auto-mode"

    async def async_press(self) -> None:
        await self.coordinator.async_set_state(pupil=None)
