#!/usr/bin/env python3
"""In-memory fake Spooky Eyes board implementing the REST part of the device API.

usage: python fake_device.py [--port 8081] [--id aabbccddeeff] [--name "Porch Eyes"]
(no /ws; the web controller doesn't need it)
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

THEMES = [{"id": t, "name": t.title()} for t in ("human", "cat", "fire", "alien", "sauron", "terminator")]
MOODS = ["neutral", "angry", "surprised", "sleepy", "asleep"]
ACTIONS = {"blink", "wink_left", "wink_right", "look", "release", "startle", "roll"}


def make_handler(info, state, started):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _full_state(self):
            return {**state, "rssi": -55, "fps": 30.0, "uptime": int(time.time() - started)}

        def do_GET(self):
            if self.path == "/api/info":
                return self._send(200, info)
            if self.path == "/api/state":
                return self._send(200, self._full_state())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            if self.path == "/api/state":
                if "theme" in body and body["theme"] not in {t["id"] for t in THEMES}:
                    return self._send(400, {"error": "unknown theme"})
                if "mood" in body and body["mood"] not in MOODS:
                    return self._send(400, {"error": "unknown mood"})
                for key in ("on", "brightness", "theme", "mood", "autonomous", "pupil"):
                    if key in body:
                        state[key] = body[key]
                return self._send(200, self._full_state())
            if self.path == "/api/action":
                if body.get("action") not in ACTIONS:
                    return self._send(400, {"error": "unknown action"})
                if body["action"] == "look":
                    state["gaze"] = {"x": body.get("x", 0), "y": body.get("y", 0)}
                elif body["action"] == "release":
                    state["gaze"] = {"x": 0, "y": 0}
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
    args = ap.parse_args()
    mac = ":".join(args.id[i:i + 2] for i in range(0, 12, 2)).upper()
    info = {
        "id": args.id, "name": args.name or f"Fake Eyes {args.id[-6:]}", "model": "fake", "fw": "0.0.0-fake",
        "mac": mac, "ip": "127.0.0.1", "themes": THEMES, "moods": MOODS, "eyes": 2,
    }
    state = {"on": True, "brightness": 200, "theme": "sauron", "mood": "neutral", "autonomous": True,
             "pupil": None, "gaze": {"x": 0.0, "y": 0.0}}
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(info, state, time.time()))
    print(f"fake board {args.id} on :{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
