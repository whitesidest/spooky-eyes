# Spooky Eyes — web controller

Django app for hands-on control of every Spooky Eyes board on the LAN: a dashboard with a card
per board, whole-fleet controls, one-click scenes, and a live gaze pad. Home Assistant remains
the automation brain; this talks to the boards over the same REST API (`/api/info`, `/api/state`,
`/api/action`).

## Run

```bash
cd webapp
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser        # optional, for Groups & scenes (Django admin)
.venv/bin/python manage.py runserver 0.0.0.0:8000
```

Add boards with **Scan network** (mDNS `_spookyeyes._tcp`), by IP on the dashboard, or from the CLI:

```bash
.venv/bin/python manage.py discover --seconds 5
```

Groups and scenes are edited in the admin (`/admin/`). A scene is a partial state
(`{"theme": "sauron", "mood": "angry"}`) plus an optional action
(`{"action": "look", "x": -1, "y": 0, "duration": 5}`), aimed at a group or every board.
Scenes show up as buttons on the dashboard.

Settings via env: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` (default `1`), `DJANGO_ALLOWED_HOSTS`
(default `*`), `DEVICE_TIMEOUT` seconds (default `3`).

## JSON API

| Method | Path | Body |
|---|---|---|
| GET | `/api/devices[?refresh=1]` | — (refresh polls every board) |
| POST | `/api/devices` | `{"host": "10.0.0.5", "port": 80}` |
| DELETE | `/api/devices/<device_id>` | — |
| POST | `/api/state` | `{"target": T, "state": {...partial}}` |
| POST | `/api/action` | `{"target": T, "action": "look", "x": 0.5, "y": 0, "duration": 1.5}` |
| POST | `/api/scenes/<id>/apply` | — |
| POST | `/api/scan` | `{"seconds": 4}` |

`T` is `{"device": "<id>"}`, `{"group": <pk>}` or `{"all": true}`. Commands fan out in parallel
and return `{"results": {"<device_id>": {"ok": true, "state": {...}} | {"ok": false, "error": "..."}}}`.

## Develop without hardware

```bash
.venv/bin/python fake_device.py --port 8081 --name "Porch Eyes" &
.venv/bin/python fake_device.py --port 8082 --id 0a0b0c0d0e0f &
# then add 127.0.0.1 port 8081 / 8082 on the dashboard
.venv/bin/python manage.py test devices
```
