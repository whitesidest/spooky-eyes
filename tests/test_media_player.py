"""The talking skull: media player, startle-sound select and theme-sounds switch."""
from __future__ import annotations

import io
import shutil
import wave
from unittest.mock import patch

import pytest
from homeassistant.components.media_player import DOMAIN as MP_DOMAIN, BrowseMedia, MediaClass
from homeassistant.components.media_source import PlayMedia
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spooky_eyes.audio import async_convert, fix_wav_sizes, wav_seconds
from custom_components.spooky_eyes.const import DOMAIN

from .conftest import BASE, DEVICE_ID, HOST, INFO, SOUNDS, STATE, mock_board, posts_to

PLAYER = "media_player.porch_eyes_voice"
STARTLE_SOUND = "select.porch_eyes_startle_sound"
THEME_SOUNDS = "switch.porch_eyes_theme_sounds"

SKULL_INFO = {
    **INFO,
    "fw": "0.3.0",
    "features": {"speaker": True, "microphone": True, "battery": False},
    "themes": [
        {"id": "human", "name": "Human", "category": "classic", "sound": None},
        {"id": "cat", "name": "Cat", "category": "creatures", "sound": None},
        {"id": "sauron", "name": "Sauron", "category": "halloween", "sound": "growl"},
        {"id": "terminator", "name": "Terminator", "category": "sci-fi", "sound": "zap"},
    ],
}
SKULL_STATE = {
    **STATE,
    "volume": 70,
    "listen": False,
    "sensitivity": 50,
    "sound_level": -60,
    "playing": None,
    "battery": None,
    "theme_sounds": True,
    "theme_sound": "growl",
}
# A speaker board on firmware that predates theme sounds.
OLD_SPEAKER_STATE = {k: v for k, v in SKULL_STATE.items() if k not in ("theme_sounds", "theme_sound")}


def tiny_wav(seconds: float = 0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(bytes(int(16000 * seconds) * 2))
    return buf.getvalue()


def uploads(aioclient_mock):
    return [c for c in aioclient_mock.mock_calls if c[0] == "POST" and c[1].path == "/api/sounds"]


async def _setup(hass, aioclient_mock, state=SKULL_STATE, sounds=SOUNDS):
    mock_board(aioclient_mock, info=SKULL_INFO, state=state, sounds=sounds)
    entry = MockConfigEntry(domain=DOMAIN, data={"host": HOST, "port": 80}, unique_id=DEVICE_ID, title="Porch Eyes")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _play(hass, media_id, media_type="music", **extra):
    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": PLAYER, "media_content_type": media_type, "media_content_id": media_id, **extra},
        blocking=True,
    )


@pytest.fixture
async def skull(hass, aioclient_mock):
    return await _setup(hass, aioclient_mock)


# ─── Media player ───────────────────────────────────────────────────────────


async def test_player_state_and_volume(hass, skull):
    state = hass.states.get(PLAYER)
    assert state.state == "idle"
    assert state.attributes["volume_level"] == 0.7
    assert state.attributes["device_class"] == "speaker"
    assert state.attributes["clips"] == ["boo"]
    assert state.attributes["builtin_sounds"][0] == "growl"

    skull.runtime_data.async_set_updated_data({**SKULL_STATE, "playing": "growl"})
    await hass.async_block_till_done()
    state = hass.states.get(PLAYER)
    assert state.state == "playing"
    assert state.attributes["media_title"] == "growl"
    assert state.attributes["media_content_id"] == "spooky_eyes://sound/growl"

    skull.runtime_data.async_set_updated_data({**SKULL_STATE, "on": False})
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).state == "off"


async def test_set_and_step_volume(hass, skull, aioclient_mock):
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": PLAYER, "volume_level": 0.25}, blocking=True
    )
    assert posts_to(aioclient_mock, "/api/state")[-1][2] == {"volume": 25}
    await hass.services.async_call("media_player", "volume_up", {"entity_id": PLAYER}, blocking=True)
    assert posts_to(aioclient_mock, "/api/state")[-1][2] == {"volume": 80}
    await hass.services.async_call("media_player", "volume_down", {"entity_id": PLAYER}, blocking=True)
    assert posts_to(aioclient_mock, "/api/state")[-1][2] == {"volume": 60}


async def test_stop(hass, skull, aioclient_mock):
    await hass.services.async_call("media_player", "media_stop", {"entity_id": PLAYER}, blocking=True)
    assert posts_to(aioclient_mock, "/api/action")[-1][2] == {"action": "stop_sound"}


@pytest.mark.parametrize(
    ("media_type", "media_id"),
    [("music", "spooky_eyes://sound/growl"), ("sound", "growl"), ("music", "boo")],
)
async def test_play_board_sound(hass, skull, aioclient_mock, media_type, media_id):
    await _play(hass, media_id, media_type)
    expected = media_id.rsplit("/", 1)[-1]
    assert posts_to(aioclient_mock, "/api/action")[-1][2] == {"action": "sound", "name": expected}
    assert not uploads(aioclient_mock)


async def test_play_unknown_sound_name_fails_without_playing(hass, skull, aioclient_mock):
    with pytest.raises(HomeAssistantError, match="no sound called"):
        await _play(hass, "nope", "sound")
    assert not posts_to(aioclient_mock, "/api/action")


async def test_play_url_downloads_converts_uploads_and_plays(hass, skull, aioclient_mock):
    aioclient_mock.get("http://example.com/boo.mp3", content=b"ID3 pretend mp3")
    with patch("custom_components.spooky_eyes.media_player.async_convert", return_value=tiny_wav(0.5)) as convert:
        await _play(hass, "http://example.com/boo.mp3")
    convert.assert_awaited_once()
    assert convert.await_args.args[1] == b"ID3 pretend mp3"
    assert convert.await_args.args[2] == ".mp3"
    sent = uploads(aioclient_mock)
    assert len(sent) == 1
    assert sent[0][1].query["name"] == "tts"
    assert posts_to(aioclient_mock, "/api/action")[-1][2] == {"action": "sound", "name": "tts"}
    assert hass.states.get(PLAYER).state == "idle"


async def test_play_media_source_resolves_first(hass, skull, aioclient_mock):
    aioclient_mock.get("http://ha.local:8123/api/tts_proxy/abc.mp3", content=b"mp3")
    with (
        patch(
            "custom_components.spooky_eyes.media_player.media_source.async_resolve_media",
            return_value=PlayMedia("http://ha.local:8123/api/tts_proxy/abc.mp3", "audio/mpeg"),
        ) as resolve,
        patch("custom_components.spooky_eyes.media_player.async_convert", return_value=tiny_wav()),
    ):
        await _play(hass, "media-source://tts/cloud?message=Boo", announce=True)
    resolve.assert_awaited_once()
    assert resolve.await_args.args[1] == "media-source://tts/cloud?message=Boo"
    assert posts_to(aioclient_mock, "/api/action")[-1][2] == {"action": "sound", "name": "tts"}


async def test_play_url_too_long_for_free_space(hass, aioclient_mock):
    await _setup(hass, aioclient_mock, sounds={**SOUNDS, "free_bytes": 16000})
    aioclient_mock.get("http://example.com/long.mp3", content=b"mp3")
    with (
        patch("custom_components.spooky_eyes.media_player.async_convert", return_value=tiny_wav(2.0)),
        pytest.raises(HomeAssistantError, match="won't fit"),
    ):
        await _play(hass, "http://example.com/long.mp3")
    assert not uploads(aioclient_mock)
    assert hass.states.get(PLAYER).state == "idle"


async def test_play_url_download_failure(hass, skull, aioclient_mock):
    aioclient_mock.get("http://example.com/missing.mp3", status=404)
    with pytest.raises(HomeAssistantError, match="Couldn't fetch"):
        await _play(hass, "http://example.com/missing.mp3")


async def test_browse_root_and_folders(hass, skull):
    player = hass.data["entity_components"][MP_DOMAIN].get_entity(PLAYER)
    root = await player.async_browse_media()
    assert root.title == "Porch Eyes"
    assert [c.title for c in root.children] == ["Built-in effects", "Uploaded clips"]
    builtin = await player.async_browse_media("music", "spooky_eyes://sounds/builtin")
    assert builtin.children[0].media_content_id == "spooky_eyes://sound/growl"
    assert builtin.children[0].title == "Growl"
    assert builtin.children[0].can_play
    clips = await player.async_browse_media("music", "spooky_eyes://sounds/clips")
    assert [c.media_content_id for c in clips.children] == ["spooky_eyes://sound/boo"]


async def test_browse_includes_media_sources_when_loaded(hass, skull):
    fake_root = BrowseMedia(
        media_class=MediaClass.DIRECTORY, media_content_id="media-source://", media_content_type="",
        title="Media Sources", can_play=False, can_expand=True,
        children=[BrowseMedia(media_class=MediaClass.DIRECTORY, media_content_id="media-source://tts",
                              media_content_type="", title="Text to speech", can_play=False, can_expand=True)],
    )
    hass.config.components.add("media_source")
    player = hass.data["entity_components"][MP_DOMAIN].get_entity(PLAYER)
    with patch(
        "custom_components.spooky_eyes.media_player.media_source.async_browse_media", return_value=fake_root
    ) as browse:
        root = await player.async_browse_media()
        assert [c.title for c in root.children] == ["Built-in effects", "Uploaded clips", "Text to speech"]
        await player.async_browse_media("", "media-source://tts")
    assert browse.await_args.args[1] == "media-source://tts"


# ─── Startle sound select and theme-sounds switch ───────────────────────────


async def test_theme_sound_entities(hass, skull):
    sel = hass.states.get(STARTLE_SOUND)
    assert sel.state == "growl"
    assert sel.attributes["options"] == ["none", "growl", "heartbeat", "whisper", "creak", "zap", "chime", "test", "boo"]
    assert sel.attributes["default_sound"] == "growl"
    assert sel.attributes["theme"] == "sauron"
    assert hass.states.get(THEME_SOUNDS).state == "on"


async def test_select_and_switch_payloads(hass, skull, aioclient_mock):
    await hass.services.async_call(
        "select", "select_option", {"entity_id": STARTLE_SOUND, "option": "boo"}, blocking=True
    )
    assert posts_to(aioclient_mock, "/api/state")[-1][2] == {"theme_sound": "boo"}
    await hass.services.async_call(
        "select", "select_option", {"entity_id": STARTLE_SOUND, "option": "none"}, blocking=True
    )
    assert posts_to(aioclient_mock, "/api/state")[-1][2] == {"theme_sound": None}
    await hass.services.async_call("switch", "turn_off", {"entity_id": THEME_SOUNDS}, blocking=True)
    assert posts_to(aioclient_mock, "/api/state")[-1][2] == {"theme_sounds": False}


async def test_select_shows_none_when_unpaired(hass, skull):
    skull.runtime_data.async_set_updated_data({**SKULL_STATE, "theme": "cat", "theme_sound": None})
    await hass.async_block_till_done()
    sel = hass.states.get(STARTLE_SOUND)
    assert sel.state == "none"
    assert sel.attributes["default_sound"] is None


async def test_unknown_pairing_refreshes_options(hass, skull, aioclient_mock):
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/sounds", json={**SOUNDS, "clips": SOUNDS["clips"] + [{"name": "scream", "bytes": 1}]})
    skull.runtime_data.async_set_updated_data({**SKULL_STATE, "theme_sound": "scream"})
    await hass.async_block_till_done()
    sel = hass.states.get(STARTLE_SOUND)
    assert sel.state == "scream"
    assert "scream" in sel.attributes["options"]
    assert "scream" in hass.states.get(PLAYER).attributes["clips"]


async def test_old_speaker_firmware_has_player_but_no_pairing(hass, aioclient_mock):
    await _setup(hass, aioclient_mock, state=OLD_SPEAKER_STATE)
    assert hass.states.get(PLAYER) is not None
    assert hass.states.get(STARTLE_SOUND) is None
    assert hass.states.get(THEME_SOUNDS) is None


async def test_board_without_speaker_has_no_player(hass, configured_entry):
    assert hass.states.get(PLAYER) is None


# ─── Conversion helpers ─────────────────────────────────────────────────────


def test_fix_wav_sizes_patches_streamed_header():
    wav = bytearray(tiny_wav(0.1))
    wav[4:8] = b"\xff\xff\xff\xff"
    wav[40:44] = b"\xff\xff\xff\xff"
    fixed = fix_wav_sizes(bytes(wav))
    with wave.open(io.BytesIO(fixed)) as w:
        assert w.getnframes() == 1600
    assert wav_seconds(fixed) == pytest.approx(0.1)
    assert fix_wav_sizes(b"not a wav") == b"not a wav"


async def test_convert_runs_ffmpeg_when_present(hass):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = await async_convert(hass, tiny_wav(0.25), ".wav")
    with wave.open(io.BytesIO(out)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.getnframes() == pytest.approx(4000, abs=50)


async def test_convert_without_ffmpeg_explains(hass):
    with patch("custom_components.spooky_eyes.audio.ffmpeg_binary", return_value=None):
        with pytest.raises(HomeAssistantError, match="ffmpeg"):
            await async_convert(hass, b"data")
