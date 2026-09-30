import json
from unittest import mock

from django.test import TestCase

from devices import client
from devices.models import Device, Group, Scene

INFO = {
    "id": "AA:BB:CC:DD:EE:01", "name": "Porch", "model": "dualeye-1.28", "fw": "0.1.0",
    "mac": "AA:BB:CC:DD:EE:01", "themes": [{"id": "sauron", "name": "Sauron"}],
    "moods": ["neutral", "angry"],
}
STATE = {"on": True, "brightness": 200, "theme": "sauron", "mood": "neutral", "autonomous": True,
         "pupil": None, "gaze": {"x": 0, "y": 0}}


def make(device_id, host):
    return Device.objects.create(device_id=device_id, name=device_id, host=host)


class ApiTestCase(TestCase):
    def post(self, path, body):
        return self.client.post(path, json.dumps(body), content_type="application/json")


class AddDeviceTests(ApiTestCase):
    @mock.patch("devices.services.client.get_state", return_value=STATE)
    @mock.patch("devices.services.client.get_info", return_value=INFO)
    def test_manual_add_normalizes_id(self, get_info, get_state):
        resp = self.post("/api/devices", {"host": "10.0.0.5"})
        self.assertEqual(resp.status_code, 201)
        get_info.assert_called_once_with("10.0.0.5", 80)
        device = Device.objects.get()
        self.assertEqual(device.device_id, "aabbccddee01")
        self.assertEqual(device.name, "Porch")
        self.assertEqual(device.last_state["theme"], "sauron")
        self.assertEqual(resp.json()["device"]["themes"], INFO["themes"])

    @mock.patch("devices.services.client.get_state", return_value=STATE)
    @mock.patch("devices.services.client.get_info", return_value=INFO)
    def test_re_adding_updates_host(self, *_):
        make("aabbccddee01", "10.0.0.1")
        self.post("/api/devices", {"host": "10.0.0.9"})
        self.assertEqual(Device.objects.get().host, "10.0.0.9")

    @mock.patch("devices.services.client.get_info", side_effect=client.DeviceError("10.0.0.5: ConnectError"))
    def test_unreachable_board(self, _):
        resp = self.post("/api/devices", {"host": "10.0.0.5"})
        self.assertEqual(resp.status_code, 502)
        self.assertFalse(Device.objects.exists())

    def test_host_required(self):
        self.assertEqual(self.post("/api/devices", {}).status_code, 400)


class FanOutTests(ApiTestCase):
    def setUp(self):
        self.a = make("000000000001", "10.0.0.1")
        self.b = make("000000000002", "10.0.0.2")
        self.c = make("000000000003", "10.0.0.3")
        self.group = Group.objects.create(name="Porch")
        self.group.devices.add(self.a, self.b)

    @mock.patch("devices.services.client.set_state")
    def test_state_to_group_only(self, set_state):
        set_state.side_effect = lambda host, port, state: {**STATE, **state}
        resp = self.post("/api/state", {"target": {"group": self.group.pk}, "state": {"theme": "cat"}})
        self.assertEqual(resp.status_code, 200)
        hosts = sorted(call.args[0] for call in set_state.call_args_list)
        self.assertEqual(hosts, ["10.0.0.1", "10.0.0.2"])
        results = resp.json()["results"]
        self.assertTrue(results["000000000001"]["ok"])
        self.a.refresh_from_db()
        self.assertEqual(self.a.last_state["theme"], "cat")
        self.assertIsNotNone(self.a.last_seen)

    @mock.patch("devices.services.client.action", return_value={"ok": True})
    def test_action_to_all_with_one_failure(self, action):
        def fake(host, port, payload):
            if host == "10.0.0.3":
                raise client.DeviceError("10.0.0.3: ConnectTimeout")
            return {"ok": True}

        action.side_effect = fake
        resp = self.post("/api/action", {"target": {"all": True}, "action": "look", "x": -0.5, "y": 0.25,
                                         "duration": 2})
        results = resp.json()["results"]
        self.assertEqual(len(results), 3)
        self.assertFalse(results["000000000003"]["ok"])
        self.assertIn("ConnectTimeout", results["000000000003"]["error"])
        action.assert_any_call("10.0.0.1", 80, {"action": "look", "x": -0.5, "y": 0.25, "duration": 2.0})

    @mock.patch("devices.services.client.action", return_value={"ok": True})
    def test_single_device_target_accepts_mac_form(self, action):
        resp = self.post("/api/action", {"target": {"device": "00:00:00:00:00:02"}, "action": "blink"})
        self.assertEqual(resp.status_code, 200)
        action.assert_called_once_with("10.0.0.2", 80, {"action": "blink"})


class ValidationTests(ApiTestCase):
    def setUp(self):
        make("000000000001", "10.0.0.1")

    def test_rejects_bad_state(self):
        for state in ({}, {"brightness": 300}, {"mood": "hangry"}, {"on": "yes"}, {"pupil": 2},
                      {"color": "red"}):
            with self.subTest(state=state):
                resp = self.post("/api/state", {"target": {"all": True}, "state": state})
                self.assertEqual(resp.status_code, 400)

    def test_rejects_bad_actions(self):
        for body in ({"action": "explode"}, {"action": "look", "x": 2}, {"action": "look", "duration": -1}):
            with self.subTest(body=body):
                resp = self.post("/api/action", {"target": {"all": True}, **body})
                self.assertEqual(resp.status_code, 400)

    def test_rejects_bad_targets(self):
        for target in (None, {}, {"device": "ffffffffffff"}, {"group": 999}):
            with self.subTest(target=target):
                resp = self.post("/api/state", {"target": target, "state": {"on": True}})
                self.assertEqual(resp.status_code, 400)

    def test_pupil_null_allowed(self):
        with mock.patch("devices.services.client.set_state", return_value=STATE) as set_state:
            resp = self.post("/api/state", {"target": {"all": True}, "state": {"pupil": None}})
        self.assertEqual(resp.status_code, 200)
        set_state.assert_called_once_with("10.0.0.1", 80, {"pupil": None})


class SceneTests(ApiTestCase):
    @mock.patch("devices.services.client.action", return_value={"ok": True})
    @mock.patch("devices.services.client.set_state", return_value=STATE)
    def test_scene_applies_state_then_action_to_group(self, set_state, action):
        a = make("000000000001", "10.0.0.1")
        make("000000000002", "10.0.0.2")
        group = Group.objects.create(name="Door")
        group.devices.add(a)
        scene = Scene.objects.create(name="Intruder", group=group, state={"theme": "sauron", "mood": "angry"},
                                     action={"action": "look", "x": -1, "y": 0, "duration": 5})
        resp = self.post(f"/api/scenes/{scene.pk}/apply", {})
        self.assertEqual(resp.status_code, 200)
        set_state.assert_called_once_with("10.0.0.1", 80, {"theme": "sauron", "mood": "angry"})
        action.assert_called_once_with("10.0.0.1", 80, {"action": "look", "x": -1, "y": 0, "duration": 5})

    @mock.patch("devices.services.client.set_state", return_value=STATE)
    def test_scene_without_group_targets_all(self, set_state):
        make("000000000001", "10.0.0.1")
        make("000000000002", "10.0.0.2")
        scene = Scene.objects.create(name="Lights out", state={"on": False})
        self.post(f"/api/scenes/{scene.pk}/apply", {})
        self.assertEqual(set_state.call_count, 2)


class PageTests(TestCase):
    def test_pages_render(self):
        make("000000000001", "10.0.0.1")
        for path in ("/", "/gaze/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_device_list(self):
        make("000000000001", "10.0.0.1")
        data = self.client.get("/api/devices").json()
        self.assertEqual(data["devices"][0]["device_id"], "000000000001")
        self.assertFalse(data["devices"][0]["online"])
