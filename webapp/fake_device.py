#!/usr/bin/env python3
"""Simulated Spooky Eyes board: the device REST API backed by the real firmware renderer.

It loads tools/sim/build/libspookysim.so (built automatically with g++ if missing), which is the
firmware's eye renderer + behaviour engine compiled for the desktop, so blinks, startles, gaze,
moods and themes behave exactly as on the hardware. It also serves a live picture of both eyes at
GET /sim/frame.png (advertised as "preview" in /api/info) for the web controller to show.

If the library can't be built it falls back to a plain in-memory fake without a preview.

usage: python fake_device.py [--port 8081] [--id aabbccddeeff] [--name "Porch Eyes"] [--theme sauron]
                             [--legacy] [--no-speaker] [--no-mic] [--no-battery] [--battery 64]
(no /ws; the web controller doesn't need it)

Renaming: POST /api/state {"name": "Porch skull"} (1-32 chars) stores the name, which /api/info and the
state then report. --legacy emulates older firmware that answers 400 "unknown field" instead and has
none of the sound/battery API.

Sound and battery (current firmware): /api/info has features {speaker, microphone, battery}; the state
carries volume, listen, sensitivity, sound_level (a slowly wandering fake room level with the odd
"bang"), playing and battery {voltage, percent} (slowly draining). /api/sounds lists/uploads/deletes
clips, kept in memory with a 3.4 MB budget; "sound", "tone" and "stop_sound" actions mark something
as playing for its duration.
"""
import argparse
import ctypes
import json
import math
import os
import random
import re
import struct
import subprocess
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(HERE, "..", "tools", "sim")
SIM_LIB = os.path.join(SIM_DIR, "build", "libspookysim.so")

EYE = 240
GAP = 20
TICK_HZ = 30

FALLBACK_THEMES = [{"id": "sauron", "name": "Sauron", "category": "halloween"}]
FALLBACK_MOODS = ["neutral", "angry", "surprised", "sleepy", "asleep"]
ACTIONS = {"blink", "wink_left", "wink_right", "look", "release", "startle", "roll"}
SOUND_ACTIONS = {"sound", "tone", "stop_sound"}
BUILTIN = {"growl": 2.2, "heartbeat": 2.6, "whisper": 2.6, "creak": 2.0, "zap": 0.9, "chime": 2.2, "test": 1.0}
SOUND_NAME = re.compile(r"^[a-z0-9_-]{1,24}$")
SOUND_BUDGET = 3_400_000  # bytes of LittleFS the real board has for clips


def wav_info(data):
    """(ok, seconds) for a 16-bit PCM WAV, mono/stereo, 8-48 kHz — the same rule the firmware applies."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False, 0
    pos, fmt, length = 12, None, 0
    while pos + 8 <= len(data):
        tag, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        if tag == b"fmt " and size >= 16:
            fmt = struct.unpack_from("<HHIIHH", data, pos + 8)
        elif tag == b"data":
            length = min(size, len(data) - pos - 8)
            break
        pos += 8 + size + (size & 1)
    if not fmt or not length:
        return False, 0
    kind, channels, rate, _, _, bits = fmt
    if kind != 1 or bits != 16 or channels not in (1, 2) or not 8000 <= rate <= 48000:
        return False, 0
    return True, length / (rate * channels * 2)


def parse_multipart(body, content_type):
    """Return the first file part's bytes from a multipart/form-data body (any field name)."""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        return body  # a raw upload
    boundary = b"--" + m.group(1).encode()
    for part in body.split(boundary)[1:]:
        if part.strip() in (b"", b"--"):
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        if b"filename=" in head:
            return data[:-2] if data.endswith(b"\r\n") else data
    return b""


def load_sim():
    """Load (building if needed) the simulator library, or return None."""
    sources = [os.path.join(SIM_DIR, "sim_api.cpp")]
    render_dir = os.path.join(HERE, "..", "firmware", "src", "render")
    sources += [os.path.join(render_dir, f) for f in os.listdir(render_dir)]
    stale = not os.path.exists(SIM_LIB) or any(os.path.getmtime(s) > os.path.getmtime(SIM_LIB) for s in sources)
    try:
        if stale:
            subprocess.run([os.path.join(SIM_DIR, "build.sh")], check=True)
        lib = ctypes.CDLL(SIM_LIB)
    except (OSError, subprocess.CalledProcessError) as err:
        print(f"simulator unavailable ({err}); running as a plain fake", flush=True)
        return None
    p, s, f, i = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_float, ctypes.c_int
    sigs = {
        "sim_theme_count": ([], i), "sim_theme_id": ([i], s), "sim_theme_name": ([i], s),
        "sim_theme_category": ([i], s), "sim_mood_count": ([], i), "sim_mood_name": ([i], s),
        "sim_new": ([ctypes.c_uint32, s], p), "sim_set_theme": ([p, s], i), "sim_set_mood": ([p, s], i),
        "sim_set_autonomous": ([p, i], None), "sim_set_pupil": ([p, f], None), "sim_look": ([p, f, f, f], None),
        "sim_release": ([p], None), "sim_blink": ([p], None), "sim_wink": ([p, i], None),
        "sim_startle": ([p], None), "sim_roll": ([p], None), "sim_update": ([p, f], None),
        "sim_target_x": ([p], f), "sim_target_y": ([p], f), "sim_render_eye": ([p, i, ctypes.c_char_p], None),
    }
    for name, (args, res) in sigs.items():
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, res
    return lib


def encode_png(width, height, rgb):
    raw = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


class Board:
    """Device state plus (optionally) the simulated eyes; thread-safe."""

    def __init__(self, lib, device_id, theme, name, legacy=False, speaker=True, mic=True, battery=64.0):
        self.lib = lib
        self.name = name
        self.legacy = legacy
        self.lock = threading.Lock()
        self.started = time.time()
        # Sound + battery (not on legacy firmware).
        self.features = {"speaker": speaker and not legacy, "microphone": mic and speaker and not legacy,
                         "battery": battery is not None and not legacy}
        self.audio = {"volume": 70, "listen": False, "sensitivity": 50}
        self.clips = {}  # name -> wav bytes
        self.playing, self.playing_until = None, 0.0
        self.battery_pct = battery if battery is not None else 0.0
        self.level_phase = random.random() * 100
        self.bang_until = 0.0
        self.next_bang = time.monotonic() + random.uniform(8, 20)
        if lib:
            self.themes = [{"id": lib.sim_theme_id(n).decode(), "name": lib.sim_theme_name(n).decode(),
                            "category": lib.sim_theme_category(n).decode()} for n in range(lib.sim_theme_count())]
            self.moods = [lib.sim_mood_name(n).decode() for n in range(lib.sim_mood_count())]
        else:
            self.themes, self.moods = FALLBACK_THEMES, FALLBACK_MOODS
        ids = {t["id"] for t in self.themes}
        theme = theme if theme in ids else self.themes[0]["id"]
        self.state = {"on": True, "brightness": 200, "theme": theme, "mood": "neutral", "autonomous": True,
                      "pupil": None, "gaze": {"x": 0.0, "y": 0.0}}
        self.fps = 0.0
        self.frame, self.frame_at = None, 0.0
        if lib:
            self.sim = lib.sim_new(int(device_id[-8:], 16) or 1, theme.encode())
            threading.Thread(target=self._tick, daemon=True).start()

    def _tick(self):
        last = time.monotonic()
        while True:
            time.sleep(1 / TICK_HZ)
            now = time.monotonic()
            with self.lock:
                self.lib.sim_update(self.sim, min(now - last, 0.1))
            self.fps = round(1 / max(now - last, 1e-3), 1)
            last = now

    def full_state(self):
        with self.lock:
            s = dict(self.state)
            if self.lib:
                s["gaze"] = {"x": round(self.lib.sim_target_x(self.sim), 2), "y": round(self.lib.sim_target_y(self.sim), 2)}
        if not self.legacy:
            s["name"] = self.name
            s.update(self.audio_state())
        return {**s, "rssi": -55, "fps": self.fps if self.state["on"] else 0, "uptime": int(time.time() - self.started)}

    # --- sound + battery -------------------------------------------------------------------------

    def sound_level(self):
        """A fake room: a slow wander around -60 dBFS with the odd bang."""
        now = time.monotonic()
        t = now + self.level_phase
        base = -62 + 7 * math.sin(t / 3.1) + 4 * math.sin(t / 0.9) + random.uniform(-2, 2)
        if now >= self.next_bang:
            self.bang_until = now + random.uniform(0.6, 1.4)
            self.next_bang = now + random.uniform(8, 20)
        if now < self.bang_until:
            base = max(base, -14 + random.uniform(-4, 4))
        if self.playing:
            base = max(base, -24 + random.uniform(-3, 3))  # the speaker is right next to the mics
        return round(max(-90.0, min(0.0, base)))

    def audio_state(self):
        out = {}
        if self.features["speaker"]:
            if self.playing and time.monotonic() > self.playing_until:
                self.playing = None
            out.update(self.audio)
            out["sound_level"] = self.sound_level() if self.features["microphone"] else -90
            out["playing"] = self.playing
            if (self.audio["listen"] and self.features["microphone"]
                    and out["sound_level"] > -18 - 0.4 * self.audio["sensitivity"]):
                self.react()
        if self.features["battery"]:
            elapsed = time.time() - self.started
            pct = max(0.0, self.battery_pct - elapsed / 90)  # ~1 % every 90 s so the badge visibly moves
            out["battery"] = {"voltage": round(3.3 + 0.9 * pct / 100, 2), "percent": int(pct)}
        else:
            out["battery"] = None
        return out

    def react(self):
        """Loud noise while listening: startle, like the firmware."""
        if self.lib:
            with self.lock:
                self.lib.sim_startle(self.sim)

    def sounds_listing(self):
        used = sum(len(w) for w in self.clips.values())
        return {"builtin": list(BUILTIN), "clips": [{"name": n, "bytes": len(w)} for n, w in sorted(self.clips.items())],
                "free_bytes": max(0, SOUND_BUDGET - used)}

    def add_clip(self, name, data):
        if not SOUND_NAME.match(name or ""):
            return "name must be 1-24 of a-z 0-9 _ -"
        ok, _ = wav_info(data)
        if not ok:
            return "not a 16-bit PCM WAV (mono/stereo, 8-48 kHz)"
        if len(data) > self.sounds_listing()["free_bytes"] + len(self.clips.get(name, b"")):
            return "upload failed (too big?)"
        self.clips[name] = data
        return None

    def remove_clip(self, name):
        return self.clips.pop(name, None) is not None

    def play(self, body):
        action = body["action"]
        if not self.features["speaker"]:
            return "audio not available"
        if action == "stop_sound":
            self.playing = None
            return None
        if action == "tone":
            hz, ms = body.get("hz", 440), body.get("ms", 500)
            if not (isinstance(hz, (int, float)) and 20 <= hz <= 8000 and isinstance(ms, (int, float)) and 10 <= ms <= 10000):
                return "tone needs hz 20-8000, ms 10-10000"
            self.playing, self.playing_until = f"tone {int(hz)} Hz", time.monotonic() + ms / 1000
            return None
        name = body.get("name")
        if name in BUILTIN:
            seconds = BUILTIN[name]
        elif isinstance(name, str) and name in self.clips:
            seconds = wav_info(self.clips[name])[1]
        else:
            return "unknown sound"
        self.playing, self.playing_until = name, time.monotonic() + seconds
        return None

    # --- state + actions -------------------------------------------------------------------------

    def apply_state(self, body):
        if "name" in body:
            if self.legacy:
                return "unknown field name"
            if not isinstance(body["name"], str) or not 1 <= len(body["name"].strip()) <= 32:
                return "name must be 1-32 characters"
            self.name = body["name"].strip()
        for key in ("volume", "listen", "sensitivity"):
            if key in body:
                if self.legacy:
                    return f"unknown field {key}"
                if key == "listen" and not isinstance(body[key], bool):
                    return "listen must be boolean"
                if key != "listen" and not (isinstance(body[key], (int, float)) and 0 <= body[key] <= 100):
                    return f"{key} must be 0-100"
                self.audio[key] = body[key] if key == "listen" else int(body[key])
        if "theme" in body and body["theme"] not in {t["id"] for t in self.themes}:
            return "unknown theme"
        if "mood" in body and body["mood"] not in self.moods:
            return "unknown mood"
        if "brightness" in body and not (isinstance(body["brightness"], (int, float)) and 0 <= body["brightness"] <= 255):
            return "brightness must be 0-255"
        if "pupil" in body and body["pupil"] is not None and not (isinstance(body["pupil"], (int, float)) and 0 <= body["pupil"] <= 1):
            return "pupil must be null or 0-1"
        with self.lock:
            for key in ("on", "brightness", "theme", "mood", "autonomous", "pupil"):
                if key in body:
                    self.state[key] = body[key]
            if self.lib:
                self.lib.sim_set_theme(self.sim, self.state["theme"].encode())
                self.lib.sim_set_mood(self.sim, self.state["mood"].encode())
                self.lib.sim_set_autonomous(self.sim, int(bool(self.state["autonomous"])))
                self.lib.sim_set_pupil(self.sim, -1.0 if self.state["pupil"] is None else float(self.state["pupil"]))
        return None

    def apply_action(self, body):
        action = body.get("action")
        if action in SOUND_ACTIONS and not self.legacy:
            return self.play(body)
        if action not in ACTIONS:
            return "unknown action"
        x, y, duration = body.get("x", 0), body.get("y", 0), body.get("duration", 0)
        if action == "look" and not (-1 <= x <= 1 and -1 <= y <= 1 and duration >= 0):
            return "x,y must be -1..1, duration >= 0"
        with self.lock:
            if action == "look":
                self.state["gaze"] = {"x": x, "y": y}
            elif action == "release":
                self.state["gaze"] = {"x": 0, "y": 0}
            if self.lib:
                sim = self.sim
                {
                    "blink": lambda: self.lib.sim_blink(sim),
                    "wink_left": lambda: self.lib.sim_wink(sim, 0),
                    "wink_right": lambda: self.lib.sim_wink(sim, 1),
                    "look": lambda: self.lib.sim_look(sim, float(x), float(y), float(duration)),
                    "release": lambda: self.lib.sim_release(sim),
                    "startle": lambda: self.lib.sim_startle(sim),
                    "roll": lambda: self.lib.sim_roll(sim),
                }[action]()
        return None

    def frame_png(self):
        """Both eyes side by side, dimmed by the backlight level; cached briefly for many viewers."""
        now = time.monotonic()
        if self.frame and now - self.frame_at < 0.04:
            return self.frame
        width = EYE * 2 + GAP
        with self.lock:
            on, level = self.state["on"], self.state["brightness"] / 255
            eyes = []
            if on:
                for eye in (0, 1):
                    buf = ctypes.create_string_buffer(EYE * EYE * 3)
                    self.lib.sim_render_eye(self.sim, eye, buf)
                    eyes.append(buf.raw)
        rgb = bytearray(width * EYE * 3)
        if on:
            dim = bytes(min(255, int(v * (0.15 + 0.85 * level))) for v in range(256)) if level < 1 else None
            gap = bytes(GAP * 3)
            for y in range(EYE):
                row = eyes[0][y * EYE * 3:(y + 1) * EYE * 3] + gap + eyes[1][y * EYE * 3:(y + 1) * EYE * 3]
                rgb[y * width * 3:(y + 1) * width * 3] = row.translate(dim) if dim else row
        self.frame, self.frame_at = encode_png(width, EYE, bytes(rgb)), now
        return self.frame


def make_handler(info, board):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/info":
                return self._send(200, {**info, "name": board.name})
            if path == "/api/state":
                return self._send(200, board.full_state())
            if path == "/sim/frame.png" and board.lib:
                return self._send(200, board.frame_png(), "image/png")
            if path == "/api/sounds" and board.features["speaker"]:
                return self._send(200, board.sounds_listing())
            self._send(404, {"error": "not found"})

        def do_DELETE(self):
            url = urlsplit(self.path)
            if url.path == "/api/sounds" and board.features["speaker"]:
                name = parse_qs(url.query).get("name", [""])[0]
                if not board.remove_clip(name):
                    return self._send(400, {"error": "unknown sound"})
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            url = urlsplit(self.path)
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if url.path == "/api/sounds" and board.features["speaker"]:
                name = parse_qs(url.query).get("name", [""])[0]
                error = board.add_clip(name, parse_multipart(raw, self.headers.get("Content-Type", "")))
                if error:
                    return self._send(400, {"error": error})
                print("clip", name, flush=True)
                return self._send(200, board.sounds_listing())
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            if url.path == "/api/state":
                error = board.apply_state(body)
                return self._send(400, {"error": error}) if error else self._send(200, board.full_state())
            if url.path == "/api/action":
                error = board.apply_action(body)
                if error:
                    return self._send(400, {"error": error})
                print("action", body, flush=True)
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--id", default="a1b2c3d4e5f6")
    ap.add_argument("--name", default=None)
    ap.add_argument("--theme", default="sauron")
    ap.add_argument("--legacy", action="store_true", help="emulate firmware without the rename/sound/battery contracts")
    ap.add_argument("--no-speaker", action="store_true", help="a board without audio (hides the sound UI)")
    ap.add_argument("--no-mic", action="store_true", help="speaker but no microphones")
    ap.add_argument("--no-battery", action="store_true", help="running from USB: battery is null")
    ap.add_argument("--battery", type=float, default=64.0, help="starting battery percent (drains slowly)")
    args = ap.parse_args()
    board = Board(load_sim(), args.id, args.theme, args.name or f"Fake Eyes {args.id[-6:]}", args.legacy,
                  speaker=not args.no_speaker, mic=not args.no_mic, battery=None if args.no_battery else args.battery)
    mac = ":".join(args.id[i:i + 2] for i in range(0, 12, 2)).upper()
    info = {
        "id": args.id, "name": board.name, "model": "simulator" if board.lib else "fake",
        "fw": "0.0.0-sim" if board.lib else "0.0.0-fake", "mac": mac, "ip": "127.0.0.1",
        "themes": board.themes, "moods": board.moods, "eyes": 2,
    }
    if not args.legacy:
        info["features"] = dict(board.features)
    if board.lib:
        info["preview"] = "/sim/frame.png"
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(info, board))
    print(f"{'simulated' if board.lib else 'fake'} board {args.id} on :{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
