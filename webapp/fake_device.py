#!/usr/bin/env python3
"""Simulated Spooky Eyes board: the device REST API backed by the real firmware renderer.

It loads tools/sim/build/libspookysim.so (built automatically with g++ if missing), which is the
firmware's eye renderer + behaviour engine compiled for the desktop, so blinks, startles, gaze,
moods and themes behave exactly as on the hardware. It also serves a live picture of both eyes at
GET /sim/frame.png (advertised as "preview" in /api/info) for the web controller to show.

If the library can't be built it falls back to a plain in-memory fake without a preview.

usage: python fake_device.py [--port 8081] [--id aabbccddeeff] [--name "Porch Eyes"] [--theme sauron]
(no /ws; the web controller doesn't need it)
"""
import argparse
import ctypes
import json
import os
import struct
import subprocess
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(HERE, "..", "tools", "sim")
SIM_LIB = os.path.join(SIM_DIR, "build", "libspookysim.so")

EYE = 240
GAP = 20
TICK_HZ = 30

FALLBACK_THEMES = [{"id": "sauron", "name": "Sauron", "category": "halloween"}]
FALLBACK_MOODS = ["neutral", "angry", "surprised", "sleepy", "asleep"]
ACTIONS = {"blink", "wink_left", "wink_right", "look", "release", "startle", "roll"}


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

    def __init__(self, lib, device_id, theme):
        self.lib = lib
        self.lock = threading.Lock()
        self.started = time.time()
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
        return {**s, "rssi": -55, "fps": self.fps if self.state["on"] else 0, "uptime": int(time.time() - self.started)}

    def apply_state(self, body):
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
                return self._send(200, info)
            if path == "/api/state":
                return self._send(200, board.full_state())
            if path == "/sim/frame.png" and board.lib:
                return self._send(200, board.frame_png(), "image/png")
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            if self.path == "/api/state":
                error = board.apply_state(body)
                return self._send(400, {"error": error}) if error else self._send(200, board.full_state())
            if self.path == "/api/action":
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
    args = ap.parse_args()
    board = Board(load_sim(), args.id, args.theme)
    mac = ":".join(args.id[i:i + 2] for i in range(0, 12, 2)).upper()
    info = {
        "id": args.id, "name": args.name or f"Fake Eyes {args.id[-6:]}", "model": "simulator" if board.lib else "fake",
        "fw": "0.0.0-sim" if board.lib else "0.0.0-fake", "mac": mac, "ip": "127.0.0.1",
        "themes": board.themes, "moods": board.moods, "eyes": 2,
    }
    if board.lib:
        info["preview"] = "/sim/frame.png"
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(info, board))
    print(f"{'simulated' if board.lib else 'fake'} board {args.id} on :{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
