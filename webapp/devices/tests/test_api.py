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


def make(device_id, host, **fields):
    return Device.objects.create(device_id=device_id, name=fields.pop("name", device_id), host=host, **fields)


class ApiTestCase(TestCase):
    def post(self, path, body):
        return self.client.post(path, json.dumps(body), content_type="application/json")

    def put(self, path, body):
        return self.client.put(path, json.dumps(body), content_type="application/json")

    def patch(self, path, body):
        return self.client.patch(path, json.dumps(body), content_type="application/json")


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
    def test_host_with_port_suffix(self, get_info, _):
        self.post("/api/devices", {"host": "127.0.0.1:8081"})
        get_info.assert_called_once_with("127.0.0.1", 8081)

    @mock.patch("devices.services.client.get_state", return_value=STATE)
    @mock.patch("devices.services.client.get_info", return_value=INFO)
    def test_re_adding_updates_host(self, *_):
        make("aabbccddee01", "10.0.0.1")
        self.post("/api/devices", {"host": "10.0.0.9"})
        self.assertEqual(Device.objects.get().host, "10.0.0.9")

    @mock.patch("devices.services.client.get_state", return_value=STATE)
    @mock.patch("devices.services.client.get_info", return_value=INFO)
    def test_re_adding_keeps_local_name(self, *_):
        make("aabbccddee01", "10.0.0.1", name="My name", name_is_local=True)
        self.post("/api/devices", {"host": "10.0.0.9"})
        self.assertEqual(Device.objects.get().name, "My name")

    @mock.patch("devices.services.client.get_info", side_effect=client.DeviceError("10.0.0.5: ConnectError"))
    def test_unreachable_board(self, _):
        resp = self.post("/api/devices", {"host": "10.0.0.5"})
        self.assertEqual(resp.status_code, 502)
        self.assertFalse(Device.objects.exists())

    def test_host_required(self):
        self.assertEqual(self.post("/api/devices", {}).status_code, 400)


class DeviceDetailTests(ApiTestCase):
    def test_get_and_delete(self):
        make("000000000001", "10.0.0.1")
        resp = self.client.get("/api/devices/000000000001")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["device"]["device_id"], "000000000001")
        self.assertEqual(self.client.delete("/api/devices/000000000001").status_code, 200)
        self.assertFalse(Device.objects.exists())
        self.assertEqual(self.client.get("/api/devices/000000000001").status_code, 404)

    @mock.patch("devices.services.client.get_state", return_value={**STATE, "name": "Porch skull"})
    def test_refresh_absorbs_board_name(self, _):
        make("000000000001", "10.0.0.1")
        resp = self.client.get("/api/devices/000000000001?refresh=1")
        self.assertEqual(resp.json()["device"]["name"], "Porch skull")
        self.assertTrue(resp.json()["device"]["online"])

    @mock.patch("devices.services.client.get_state", return_value={**STATE, "name": "Board name"})
    def test_refresh_never_overwrites_local_name(self, _):
        make("000000000001", "10.0.0.1", name="Mine", name_is_local=True)
        self.client.get("/api/devices?refresh=1")
        self.assertEqual(Device.objects.get().name, "Mine")


class RenameTests(ApiTestCase):
    def setUp(self):
        self.device = make("000000000001", "10.0.0.1", info={"name": "Old"})

    @mock.patch("devices.services.client.set_state")
    def test_rename_on_board(self, set_state):
        set_state.side_effect = lambda host, port, state: {**STATE, **state}
        resp = self.patch("/api/devices/000000000001", {"name": "  Porch   skull "})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["on_board"])
        set_state.assert_called_once_with("10.0.0.1", 80, {"name": "Porch skull"})
        self.device.refresh_from_db()
        self.assertEqual(self.device.name, "Porch skull")
        self.assertFalse(self.device.name_is_local)
        self.assertEqual(self.device.info["name"], "Porch skull")

    @mock.patch("devices.services.client.set_state", side_effect=client.DeviceError("10.0.0.1: HTTP 400 unknown field name"))
    def test_firmware_rejects_name_falls_back_locally(self, _):
        resp = self.patch("/api/devices/000000000001", {"name": "Porch skull"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["on_board"])
        self.assertIn("saved here", resp.json()["note"])
        self.device.refresh_from_db()
        self.assertEqual(self.device.name, "Porch skull")
        self.assertTrue(self.device.name_is_local)

    @mock.patch("devices.services.client.set_state", return_value=STATE)
    def test_firmware_ignores_name_falls_back_locally(self, _):
        resp = self.patch("/api/devices/000000000001", {"name": "Porch skull"})
        self.assertFalse(resp.json()["on_board"])
        self.device.refresh_from_db()
        self.assertTrue(self.device.name_is_local)
        self.assertEqual(self.device.name, "Porch skull")

    @mock.patch("devices.services.client.set_state", side_effect=client.DeviceError("10.0.0.1: ConnectError"))
    def test_offline_board_keeps_name_locally(self, _):
        resp = self.patch("/api/devices/000000000001", {"name": "Porch skull"})
        self.assertFalse(resp.json()["on_board"])
        self.assertIn("didn't answer", resp.json()["note"])
        self.assertEqual(Device.objects.get().name, "Porch skull")

    def test_rejects_bad_names(self):
        for name in ("", "   ", "x" * 33, 5):
            with self.subTest(name=name):
                self.assertEqual(self.patch("/api/devices/000000000001", {"name": name}).status_code, 400)


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
                      {"color": "red"}, {"name": "Porch"}):
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


class SceneApplyTests(ApiTestCase):
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


class SceneCrudTests(ApiTestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Porch")

    def test_create_list_update_delete(self):
        resp = self.post("/api/scenes", {"name": "Intruder", "group": self.group.pk,
                                         "state": {"theme": "sauron", "mood": "angry", "brightness": 255.0},
                                         "action": {"action": "look", "x": -0.8, "y": 0.1, "duration": 8}})
        self.assertEqual(resp.status_code, 201, resp.content)
        scene = resp.json()["scene"]
        self.assertEqual(scene["group_name"], "Porch")
        self.assertEqual(scene["state"], {"theme": "sauron", "mood": "angry", "brightness": 255})
        self.assertEqual(scene["action"]["duration"], 8.0)

        self.assertEqual([s["name"] for s in self.client.get("/api/scenes").json()["scenes"]], ["Intruder"])

        resp = self.put(f"/api/scenes/{scene['id']}", {"name": "Intruder!", "group": None, "state": {"on": False}})
        self.assertEqual(resp.status_code, 200, resp.content)
        updated = resp.json()["scene"]
        self.assertEqual((updated["name"], updated["group"], updated["state"], updated["action"]),
                         ("Intruder!", None, {"on": False}, None))

        self.assertEqual(self.client.delete(f"/api/scenes/{scene['id']}").status_code, 200)
        self.assertFalse(Scene.objects.exists())

    def test_validation(self):
        for body in ({"name": "x"},                                   # nothing to apply
                     {"name": "", "state": {"on": True}},             # no name
                     {"name": "x", "state": {"mood": "hangry"}},      # bad state
                     {"name": "x", "action": {"action": "explode"}},  # bad action
                     {"name": "x", "group": 999, "state": {"on": True}},
                     {"name": "x", "group": "porch", "state": {"on": True}}):
            with self.subTest(body=body):
                self.assertEqual(self.post("/api/scenes", body).status_code, 400)
        self.assertEqual(self.post("/api/scenes", {"name": "Dup", "state": {"on": True}}).status_code, 201)
        self.assertEqual(self.post("/api/scenes", {"name": "Dup", "state": {"on": True}}).status_code, 400)

    def test_action_only_scene(self):
        resp = self.post("/api/scenes", {"name": "Boo", "action": {"action": "startle"}})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["scene"]["state"], {})


class GroupCrudTests(ApiTestCase):
    def setUp(self):
        self.a = make("000000000001", "10.0.0.1")
        self.b = make("000000000002", "10.0.0.2")

    def test_create_list_update_delete(self):
        resp = self.post("/api/groups", {"name": "Porch", "devices": ["000000000001", "ffffffffffff"]})
        self.assertEqual(resp.status_code, 201, resp.content)
        group = resp.json()["group"]
        self.assertEqual(group["devices"], ["000000000001"])  # unknown ids are ignored
        self.assertEqual(self.client.get("/api/groups").json()["groups"][0]["name"], "Porch")

        resp = self.put(f"/api/groups/{group['id']}", {"name": "Front", "devices": ["000000000002"]})
        self.assertEqual(resp.json()["group"], {"id": group["id"], "name": "Front", "devices": ["000000000002"]})
        self.assertEqual(self.client.get("/api/devices").json()["devices"][1]["groups"], [group["id"]])

        scene = Scene.objects.create(name="S", group_id=group["id"], state={"on": True})
        self.assertEqual(self.client.delete(f"/api/groups/{group['id']}").status_code, 200)
        scene.refresh_from_db()
        self.assertIsNone(scene.group)  # scene now targets every board

    def test_validation(self):
        self.assertEqual(self.post("/api/groups", {"name": ""}).status_code, 400)
        self.assertEqual(self.post("/api/groups", {"name": "x", "devices": "nope"}).status_code, 400)
        self.assertEqual(self.post("/api/groups", {"name": "Dup"}).status_code, 201)
        self.assertEqual(self.post("/api/groups", {"name": "Dup"}).status_code, 400)


class PageTests(TestCase):
    def test_pages_render(self):
        make("000000000001", "10.0.0.1")
        for path in ("/", "/boards/000000000001/", "/puppeteer/", "/scenes/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_old_gaze_url_redirects(self):
        self.assertRedirects(self.client.get("/gaze/"), "/puppeteer/", status_code=301)

    def test_unknown_board_404(self):
        self.assertEqual(self.client.get("/boards/ffffffffffff/").status_code, 404)

    def test_empty_state(self):
        resp = self.client.get("/")
        self.assertContains(resp, "No boards yet")
        self.assertContains(resp, "SpookyEyes-xxxxxx")

    def test_board_page_has_theme_gallery(self):
        d = make("000000000001", "10.0.0.1")
        d.info = {"themes": [{"id": "sauron", "name": "Sauron", "category": "halloween"},
                             {"id": "mystery", "name": "Mystery"}]}
        d.save()
        resp = self.client.get("/boards/000000000001/")
        self.assertContains(resp, 'data-theme="sauron"')
        self.assertContains(resp, "devices/themes/sauron_s.png")
        self.assertContains(resp, 'data-theme="mystery"')  # unknown theme still listed, without a picture

    def test_device_list(self):
        make("000000000001", "10.0.0.1")
        data = self.client.get("/api/devices").json()
        self.assertEqual(data["devices"][0]["device_id"], "000000000001")
        self.assertFalse(data["devices"][0]["online"])
        self.assertFalse(data["devices"][0]["name_is_local"])

    def test_theme_catalog(self):
        data = self.client.get("/api/themes").json()
        ids = [t["id"] for t in data["themes"]]
        self.assertIn("sauron", ids)
        sauron = next(t for t in data["themes"] if t["id"] == "sauron")
        self.assertEqual(sauron["category"], "halloween")
        self.assertTrue(sauron["png"].endswith("sauron.png"))


class PreviewTests(ApiTestCase):
    def test_board_without_preview(self):
        make("000000000001", "10.0.0.1")
        self.assertEqual(self.client.get("/api/devices/000000000001/preview").status_code, 404)

    @mock.patch("devices.views.client.get_preview", return_value=(b"\x89PNG...", "image/png"))
    def test_simulated_board_proxies_picture(self, get_preview):
        d = make("000000000001", "127.0.0.1")
        d.port, d.info = 8081, {"preview": "/sim/frame.png"}
        d.save()
        resp = self.client.get("/api/devices/000000000001/preview")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertEqual(resp.content, b"\x89PNG...")
        get_preview.assert_called_once_with("127.0.0.1", 8081, "/sim/frame.png")

    def test_rejects_non_path_preview(self):
        d = make("000000000001", "10.0.0.1")
        d.info = {"preview": "//evil.example/x.png"}
        d.save()
        self.assertEqual(self.client.get("/api/devices/000000000001/preview").status_code, 404)

    def test_pages_flag_previews(self):
        d = make("000000000001", "127.0.0.1")
        d.info = {"preview": "/sim/frame.png"}
        d.save()
        make("000000000002", "10.0.0.2")
        resp = self.client.get("/puppeteer/")
        self.assertEqual(resp.status_code, 200)
        flags = {x["device_id"]: x["preview"] for x in resp.context["snapshot"]["devices"]}
        self.assertEqual(flags, {"000000000001": True, "000000000002": False})
