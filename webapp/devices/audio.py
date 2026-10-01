"""Converts uploaded audio into what the board plays: 16 kHz mono 16-bit PCM WAV.

With ffmpeg on the server any common format works (mp3, m4a, ogg, flac, wav...). Without it only
PCM WAV files are accepted and converted in pure Python (`audioop` is gone in 3.13, so the
downmix and resampling are done here by hand; they are simple and the clips are short).
"""
from __future__ import annotations

import array
import io
import shutil
import struct
import subprocess
import tempfile
import wave
from functools import lru_cache

TARGET_RATE = 16000
BYTES_PER_SECOND = TARGET_RATE * 2  # mono 16-bit
MAX_SECONDS = 120  # longer than the board can store anyway (~100 s)
FFMPEG_TIMEOUT = 60


class AudioError(ValueError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def accepted_formats() -> str:
    return "any common audio file" if ffmpeg_path() else "WAV files (install ffmpeg for mp3 and the rest)"


def convert(data: bytes, filename: str = "", use_ffmpeg: bool | None = None) -> bytes:
    """Return a 16 kHz mono 16-bit PCM WAV for `data` (any format with ffmpeg; PCM WAV otherwise)."""
    if not data:
        raise AudioError("the file is empty")
    if use_ffmpeg is None:
        use_ffmpeg = ffmpeg_path() is not None
    if use_ffmpeg:
        return _convert_ffmpeg(data, filename)
    return convert_wav(data)


def _convert_ffmpeg(data: bytes, filename: str) -> bytes:
    # Some containers (m4a/mp4) need a seekable input, so go through a temp file rather than a pipe.
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not suffix.strip(".").isalnum():
        suffix = ""
    with tempfile.NamedTemporaryFile(suffix=suffix) as src:
        src.write(data)
        src.flush()
        cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-i", src.name, "-vn", "-map_metadata", "-1",
               "-t", str(MAX_SECONDS), "-ac", "1", "-ar", str(TARGET_RATE), "-acodec", "pcm_s16le",
               "-fflags", "+bitexact", "-f", "wav", "pipe:1"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            raise AudioError("converting took too long; try a shorter clip") from None
    if proc.returncode != 0 or len(proc.stdout) < 44:
        detail = proc.stderr.decode(errors="replace").strip().splitlines()
        raise AudioError("couldn't read that audio file" + (f" ({detail[-1]})" if detail else ""))
    return _fix_sizes(proc.stdout)


def _fix_sizes(wav: bytes) -> bytes:
    """ffmpeg writing to a pipe leaves the RIFF/data sizes as 0xFFFFFFFF; the board wants real ones."""
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


def convert_wav(data: bytes) -> bytes:
    """Pure-Python path: PCM WAV (8/16/24/32-bit, any channels, any rate) -> 16 kHz mono 16-bit."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if w.getcomptype() != "NONE":
                raise AudioError("only uncompressed PCM WAV files are accepted")
            raw = w.readframes(min(frames, rate * MAX_SECONDS))
    except (wave.Error, EOFError, struct.error) as err:
        raise AudioError(f"not a WAV file I can read ({err})") from None
    if channels < 1 or rate < 1 or width not in (1, 2, 3, 4):
        raise AudioError("unsupported WAV layout")
    samples = _to_int16(raw, width)
    if channels > 1:
        samples = _downmix(samples, channels)
    if rate != TARGET_RATE:
        samples = _resample(samples, rate, TARGET_RATE)
    return pack_wav(samples)


def _to_int16(raw: bytes, width: int) -> array.array:
    if width == 2:
        out = array.array("h")
        out.frombytes(raw[: len(raw) - len(raw) % 2])
        return out
    if width == 1:  # unsigned 8-bit
        return array.array("h", ((b - 128) << 8 for b in raw))
    if width == 4:
        src = array.array("i")
        src.frombytes(raw[: len(raw) - len(raw) % 4])
        return array.array("h", (v >> 16 for v in src))
    # 24-bit little-endian, signed
    n = len(raw) // 3
    return array.array("h", (int.from_bytes(raw[i * 3:i * 3 + 3], "little", signed=True) >> 8 for i in range(n)))


def _downmix(samples: array.array, channels: int) -> array.array:
    n = len(samples) // channels
    return array.array("h", (sum(samples[i * channels:(i + 1) * channels]) // channels for i in range(n)))


def _resample(samples: array.array, src_rate: int, dst_rate: int) -> array.array:
    """Linear interpolation; fine for speech and spooky noises at 16 kHz."""
    n_in = len(samples)
    if n_in < 2:
        return array.array("h")
    n_out = max(1, round(n_in * dst_rate / src_rate))
    step = (n_in - 1) / max(n_out - 1, 1)
    out = array.array("h", bytes(2 * n_out))
    for i in range(n_out):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = samples[j]
        b = samples[j + 1] if j + 1 < n_in else a
        out[i] = int(a + (b - a) * frac)
    return out


def pack_wav(samples: array.array, rate: int = TARGET_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def wav_seconds(wav: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav)) as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError):
        return 0.0


def human_size(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "kB", "MB"):
        if n < 1000 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} MB"
