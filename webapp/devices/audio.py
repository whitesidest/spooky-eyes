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
URL_TIMEOUT = 20

# Voice effects for push-to-talk, as ffmpeg -af chains. Every chain starts by settling on 16 kHz
# (so the pitch tricks below do their maths on a known rate) and ends with loudnorm, which lifts a
# quiet phone recording to a level the little speaker can use (dynaudnorm needs longer clips).
#  - pitch shift = asetrate (changes pitch and speed) + aresample back + atempo to undo the speed
#  - demon: ~0.75x pitch, short slap-back echoes for a cavern; ghost: ~1.2x pitch, long airy
#    echoes and a slow tremolo; robot: phase-zeroed FFT frames (a monotone buzz) through a band-pass
_PRE = "aresample=16000,"
_POST = ",loudnorm=I=-16:TP=-1.5:LRA=11,aresample=16000"
EFFECTS = {
    "natural": _PRE + "highpass=f=80" + _POST,
    "demon": _PRE + "asetrate=12000,aresample=16000,atempo=1.3333,aecho=0.8:0.9:40|90:0.4|0.25,highpass=f=60" + _POST,
    "ghost": (_PRE + "asetrate=19200,aresample=16000,atempo=0.8333,aecho=0.7:0.8:120|260:0.5|0.3,"
              "tremolo=f=5.5:d=0.35,highpass=f=150" + _POST),
    "robot": (_PRE + "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75,"
              "highpass=f=200,lowpass=f=4000" + _POST),
}
EFFECT_LABELS = {"natural": "Natural", "demon": "Demon", "ghost": "Ghost", "robot": "Robot"}


class AudioError(ValueError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def accepted_formats() -> str:
    return "any common audio file" if ffmpeg_path() else "WAV files (install ffmpeg for mp3 and the rest)"


def convert(data: bytes, filename: str = "", use_ffmpeg: bool | None = None, effect: str | None = None) -> bytes:
    """Return a 16 kHz mono 16-bit PCM WAV for `data` (any format with ffmpeg; PCM WAV otherwise).

    `effect` (a key of EFFECTS) runs the audio through that voice filter; it needs ffmpeg, except that
    "natural" without ffmpeg falls back to the plain WAV conversion.
    """
    if not data:
        raise AudioError("the file is empty")
    if effect is not None and effect not in EFFECTS:
        raise AudioError(f"unknown voice effect {effect!r}")
    if use_ffmpeg is None:
        use_ffmpeg = ffmpeg_path() is not None
    if use_ffmpeg:
        return _convert_ffmpeg(data, filename, effect)
    if effect not in (None, "natural"):
        raise AudioError("voice effects need ffmpeg on the server")
    return convert_wav(data)


def _convert_ffmpeg(data: bytes, filename: str, effect: str | None = None) -> bytes:
    # Some containers (m4a/mp4) need a seekable input, so go through a temp file rather than a pipe.
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not suffix.strip(".").isalnum():
        suffix = ""
    with tempfile.NamedTemporaryFile(suffix=suffix) as src:
        src.write(data)
        src.flush()
        cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-i", src.name, "-vn", "-map_metadata", "-1",
               "-t", str(MAX_SECONDS)]
        if effect:
            cmd += ["-af", EFFECTS[effect]]
        cmd += ["-ac", "1", "-ar", str(TARGET_RATE), "-acodec", "pcm_s16le", "-fflags", "+bitexact", "-f", "wav", "pipe:1"]
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


def fetch_url(url: str, max_bytes: int) -> tuple[bytes, str]:
    """Download an audio file for "play from URL". Returns (data, a filename hint for the converter)."""
    import httpx  # noqa: PLC0415 - keeps this module importable without Django/httpx for the WAV helpers

    try:
        with httpx.stream("GET", url, timeout=URL_TIMEOUT, follow_redirects=True) as resp:
            if resp.status_code >= 400:
                raise AudioError(f"that address answered HTTP {resp.status_code}")
            chunks, total = [], 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise AudioError(f"that file is too large to convert (limit {human_size(max_bytes)})")
                chunks.append(chunk)
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    except httpx.HTTPError as err:
        raise AudioError(f"couldn't fetch that address ({err.__class__.__name__})") from None
    data = b"".join(chunks)
    if not data:
        raise AudioError("that address returned nothing")
    name = url.split("?", 1)[0].rsplit("/", 1)[-1]
    if "." not in name:
        ext = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav",
               "audio/flac": "flac", "audio/aac": "aac", "audio/webm": "webm", "video/webm": "webm"}.get(ctype)
        name = f"download.{ext}" if ext else "download"
    return data, name


def human_size(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "kB", "MB"):
        if n < 1000 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} MB"
