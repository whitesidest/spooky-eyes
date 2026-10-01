"""Turns whatever Home Assistant hands the media player (TTS mp3, a stream URL) into board audio.

The board plays 16-bit PCM WAV only, so anything else goes through ffmpeg: Home Assistant's
own `ffmpeg` integration if it is loaded, otherwise an `ffmpeg` found on PATH (the official
images ship one). Output is 16 kHz mono, capped at a minute, which is what the board stores
comfortably (~32 kB per second).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import struct
import tempfile

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)

TARGET_RATE = 16000
MAX_SECONDS = 60
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
FFMPEG_TIMEOUT = 60


def ffmpeg_binary(hass: HomeAssistant) -> str | None:
    """The ffmpeg integration's binary when it is set up, else one from PATH, else None."""
    try:
        from homeassistant.components.ffmpeg import get_ffmpeg_manager  # noqa: PLC0415

        return get_ffmpeg_manager(hass).binary
    except (ImportError, ValueError, KeyError):
        return shutil.which("ffmpeg")


async def async_convert(hass: HomeAssistant, data: bytes, suffix: str = "") -> bytes:
    """Return `data` (any audio ffmpeg can read) as a 16 kHz mono 16-bit PCM WAV."""
    if not data:
        raise HomeAssistantError("The audio to play is empty.")
    binary = await hass.async_add_executor_job(ffmpeg_binary, hass)
    if not binary:
        raise HomeAssistantError(
            "ffmpeg is needed to play streamed or spoken audio on the board; install it or enable the ffmpeg integration."
        )
    # Some containers need a seekable input, so the source goes through a temp file; output is piped.
    path = await hass.async_add_executor_job(_write_temp, data, suffix)
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "-nostdin", "-v", "error", "-i", path, "-vn", "-map_metadata", "-1",
            "-t", str(MAX_SECONDS), "-ac", "1", "-ar", str(TARGET_RATE), "-acodec", "pcm_s16le",
            "-fflags", "+bitexact", "-f", "wav", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), FFMPEG_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            raise HomeAssistantError("Converting the audio took too long.") from None
    finally:
        await hass.async_add_executor_job(_unlink, path)
    if proc.returncode != 0 or len(out) < 44:
        detail = err.decode(errors="replace").strip().splitlines()
        raise HomeAssistantError("ffmpeg couldn't read that audio" + (f" ({detail[-1]})" if detail else "") + ".")
    return fix_wav_sizes(out)


def _write_temp(data: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(prefix="spooky_eyes_", suffix=suffix if suffix.strip(".").isalnum() else "")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def fix_wav_sizes(wav: bytes) -> bytes:
    """ffmpeg writing to a pipe leaves RIFF/data sizes as 0xFFFFFFFF; the board wants real ones."""
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        return wav
    pos = 12
    while pos + 8 <= len(wav):
        tag, size = wav[pos:pos + 4], struct.unpack_from("<I", wav, pos + 4)[0]
        if tag == b"data":
            size = len(wav) - pos - 8
            return (wav[:4] + struct.pack("<I", len(wav) - 8) + wav[8:pos + 4] + struct.pack("<I", size)
                    + wav[pos + 8:])
        pos += 8 + size + (size & 1)
    return wav


def wav_seconds(wav: bytes) -> float:
    """Length of a 16 kHz mono 16-bit WAV (good enough for a size check)."""
    return max(0, len(wav) - 44) / (TARGET_RATE * 2)
