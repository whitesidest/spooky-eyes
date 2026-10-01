"""The talking skull: a media player over the board's speaker.

Built-in effects and uploaded clips play straight from the board. Anything else Home Assistant
hands over (TTS, a media-source file, a URL) is downloaded, converted to 16 kHz mono WAV and
uploaded as the clip `tts`, then played, so `tts.speak`, `media_player.play_media` and
announcements all come out of the skull.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpookyEyesConfigEntry
from .api import SpookyEyesApiError
from .audio import MAX_DOWNLOAD_BYTES, async_convert, wav_seconds
from .const import (
    ACTION_SOUND,
    ACTION_STOP_SOUND,
    MEDIA_FOLDER_PREFIX,
    MEDIA_SOUND_PREFIX,
    SOUND_NAME_RE,
    TTS_CLIP,
)
from .coordinator import SpookyEyesCoordinator
from .entity import SpookyEyesEntity

_LOGGER = logging.getLogger(__name__)

VOLUME_STEP = 0.1
_SOUND_NAME = re.compile(SOUND_NAME_RE)
# Media types under which a bare sound name is accepted as media_id.
_SOUND_TYPES = {"sound", "spooky_eyes/sound", MediaType.MUSIC, MediaType.TRACK}


async def async_setup_entry(
    hass: HomeAssistant, entry: SpookyEyesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.has_speaker:
        async_add_entities([SpookyEyesMediaPlayer(coordinator, "voice")])


class SpookyEyesMediaPlayer(SpookyEyesEntity, MediaPlayerEntity):
    _attr_translation_key = "voice"
    _attr_icon = "mdi:skull"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, coordinator: SpookyEyesCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._busy = False

    # ─── State ──────────────────────────────────────────────────────────────

    @property
    def state(self) -> MediaPlayerState | None:
        s = self.state_data
        if not s:
            return None
        if s.get("on") is False:
            return MediaPlayerState.OFF
        if self._busy or s.get("playing"):
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float | None:
        volume = self.state_data.get("volume")
        return None if volume is None else max(0.0, min(1.0, volume / 100))

    @property
    def media_title(self) -> str | None:
        return self.state_data.get("playing") or None

    @property
    def media_content_id(self) -> str | None:
        playing = self.state_data.get("playing")
        return f"{MEDIA_SOUND_PREFIX}{playing}" if playing and _SOUND_NAME.match(playing) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sounds = self.coordinator.sounds
        return {
            "builtin_sounds": list(sounds.get("builtin") or []),
            "clips": [c["name"] for c in sounds.get("clips") or []],
            "free_bytes": sounds.get("free_bytes"),
        }

    # ─── Commands ───────────────────────────────────────────────────────────

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.async_set_state(volume=int(round(max(0.0, min(1.0, volume)) * 100)))

    async def async_volume_up(self) -> None:
        await self.async_set_volume_level(min(1.0, (self.volume_level or 0) + VOLUME_STEP))

    async def async_volume_down(self) -> None:
        await self.async_set_volume_level(max(0.0, (self.volume_level or 0) - VOLUME_STEP))

    async def async_media_stop(self) -> None:
        await self.coordinator.async_action(ACTION_STOP_SOUND)

    async def async_play_media(self, media_type: MediaType | str, media_id: str, **kwargs: Any) -> None:
        """A board sound by id/name plays as is; anything else is fetched, converted and uploaded."""
        if media_id.startswith(MEDIA_SOUND_PREFIX):
            return await self._play_sound(media_id[len(MEDIA_SOUND_PREFIX):])
        if media_type in _SOUND_TYPES and _SOUND_NAME.match(media_id) and "://" not in media_id:
            if media_id not in self.coordinator.sound_names:
                try:
                    await self.coordinator.async_fetch_sounds()
                except SpookyEyesApiError:
                    pass
            if media_id in self.coordinator.sound_names:
                return await self._play_sound(media_id)
            raise HomeAssistantError(f"The board has no sound called “{media_id}”.")

        url = media_id
        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            url = play_item.url
        url = async_process_play_media_url(self.hass, url)

        self._busy = True
        self.async_write_ha_state()
        try:
            data, suffix = await self._download(url)
            wav = await async_convert(self.hass, data, suffix)
            await self._upload(TTS_CLIP, wav)
            await self.coordinator.async_action(ACTION_SOUND, name=TTS_CLIP)
        finally:
            self._busy = False
            self.async_write_ha_state()

    async def _play_sound(self, name: str) -> None:
        if not _SOUND_NAME.match(name):
            raise HomeAssistantError(f"“{name}” is not a valid sound name (1-24 of a-z, 0-9, _ and -).")
        await self.coordinator.async_action(ACTION_SOUND, name=name)

    async def _download(self, url: str) -> tuple[bytes, str]:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status >= 400:
                    raise HomeAssistantError(f"Couldn't fetch the audio ({resp.status}) from {url}")
                data = await resp.content.read(MAX_DOWNLOAD_BYTES + 1)
        except aiohttp.ClientError as err:
            raise HomeAssistantError(f"Couldn't fetch the audio from {url}: {err}") from err
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise HomeAssistantError("That audio file is too large to play on the board (25 MB limit).")
        path = url.split("?", 1)[0]
        suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        return data, suffix

    async def _upload(self, name: str, wav: bytes) -> None:
        coordinator = self.coordinator
        sounds = coordinator.sounds
        free = int(sounds.get("free_bytes") or 0)
        replacing = next((int(c.get("bytes") or 0) for c in sounds.get("clips") or [] if c.get("name") == name), 0)
        if free and len(wav) > free + replacing:
            fits = (free + replacing) / 32000
            raise HomeAssistantError(
                f"{wav_seconds(wav):.0f} s of audio won't fit: the board has room for about {fits:.0f} s. "
                "Delete a clip from the web controller or play something shorter."
            )
        try:
            listing = await coordinator.client.upload_sound(name, wav)
        except SpookyEyesApiError as err:
            raise HomeAssistantError(f"Spooky Eyes {coordinator.client.host}: {err}") from err
        if isinstance(listing, dict):
            coordinator.set_sounds(listing)

    # ─── Browse ─────────────────────────────────────────────────────────────

    async def async_browse_media(
        self, media_content_type: MediaType | str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        if media_content_id and media_source.is_media_source_id(media_content_id):
            return await media_source.async_browse_media(self.hass, media_content_id, content_filter=_is_audio)
        if media_content_id and media_content_id.startswith(MEDIA_FOLDER_PREFIX):
            return self._browse_folder(media_content_id[len(MEDIA_FOLDER_PREFIX):])
        return await self._browse_root()

    async def _browse_root(self) -> BrowseMedia:
        if not self.coordinator.sounds:
            try:
                await self.coordinator.async_fetch_sounds(notify=False)
            except SpookyEyesApiError:
                pass
        children = [self._browse_folder("builtin"), self._browse_folder("clips")]
        if "media_source" in self.hass.config.components:
            try:
                root = await media_source.async_browse_media(self.hass, None, content_filter=_is_audio)
            except Exception as err:  # noqa: BLE001 - media sources are optional here
                _LOGGER.debug("media_source browse failed: %s", err)
            else:
                children.extend(root.children or [])
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=f"{MEDIA_FOLDER_PREFIX}root",
            media_content_type=MediaType.MUSIC,
            title=self.coordinator.info.get("name") or "Spooky Eyes",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    def _browse_folder(self, folder: str) -> BrowseMedia:
        sounds = self.coordinator.sounds
        if folder == "clips":
            names = sorted(c["name"] for c in sounds.get("clips") or [])
            title = "Uploaded clips"
        else:
            folder = "builtin"
            names = list(self.coordinator.sound_names[: len(sounds.get("builtin") or [])])
            title = "Built-in effects"
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=f"{MEDIA_FOLDER_PREFIX}{folder}",
            media_content_type=MediaType.MUSIC,
            title=title,
            can_play=False,
            can_expand=True,
            children=[
                BrowseMedia(
                    media_class=MediaClass.TRACK,
                    media_content_id=f"{MEDIA_SOUND_PREFIX}{name}",
                    media_content_type=MediaType.MUSIC,
                    title=name.replace("_", " ").capitalize() if folder == "builtin" else name,
                    can_play=True,
                    can_expand=False,
                )
                for name in names
            ],
            children_media_class=MediaClass.TRACK,
        )


def _is_audio(item: BrowseMedia) -> bool:
    return item.media_content_type.startswith("audio/")
