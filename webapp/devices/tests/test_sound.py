"""Sound + battery: validation, the sound proxies, audio conversion and scenes that play sounds."""
import array
import io
import json
import math
import wave
from unittest import mock

from django.test import TestCase, override_settings

from devices import audio, client
from devices.models import Device, Group, Scene
from devices.validation import ValidationError, clean_action, clean_scene, clean_state, suggest_sound_name

SPEAKER_INFO = {"id": "a1b2c3000091", "name": "Porch", "themes": [{"id": "sauron", "name": "Sauron"}],
                "features": {"speaker": True, "microphone": True, "battery": True}}
LISTING = {"builtin": ["growl", "heartbeat", "whisper", "creak", "zap", "chime", "test"],
           "clips": [{"name": "boo", "bytes": 64000}], "free_bytes": 3_000_000}
STATE = {"on": True, "brightness": 200, "theme": "sauron", "mood": "neutral", "autonomous": True, "pupil": None,
         "gaze": {"x": 0, "y": 0}, "volume": 70, "listen": False, "sensitivity": 50, "sound_level": -61,
         "playing": None, "battery": {"voltage": 3.91, "percent": 64}}


def make_wav(seconds=1.0, rate=44100, channels=2, width=2, hz=440.0):
    """A sine WAV of the given layout."""
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        v = math.sin(2 * math.pi * hz * i / rate)
        for ch in range(channels):
            s = v * (1.0 if ch == 0 else 0.5)
            if width == 1:
                frames += bytes([int(128 + 127 * s)])
            elif width == 2:
                frames += int(32767 * s).to_bytes(2, "little", signed=True)
            elif width == 3:
                frames += int(8388607 * s).to_bytes(3, "little", signed=True)
            else:
                frames += int(2147483647 * s).to_bytes(4, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def wav_layout(data):
    with wave.open(io.BytesIO(data)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


def make(device_id, host, **fields):
    return Device.objects.create(device_id=device_id, name=fields.pop("name", device_id), host=host, **fields)


class StateValidationTests(TestCase):
    def test_new_fields_accepted_and_coerced(self):
        self.assertEqual(clean_state({"volume": 55.0, "listen": True, "sensitivity": 80}),
                         {"volume": 55, "listen": True, "sensitivity": 80})

    def test_new_fields_rejected_out_of_range(self):
        for state in ({"volume": 101}, {"volume": -1}, {"volume": "loud"}, {"listen": "yes"}, {"listen": 1},
                      {"sensitivity": 150}, {"sensitivity": None}):
            with self.subTest(state=state):
                with self.assertRaises(ValidationError):
                    clean_state(state)


class ActionValidationTests(TestCase):
    def test_sound_action(self):
        self.assertEqual(clean_action({"action": "sound", "name": "growl"}), {"action": "sound", "name": "growl"})
        self.assertEqual(clean_action({"action": "sound", "name": "my-clip_2"}), {"action": "sound", "name": "my-clip_2"})

    def test_sound_name_rule(self):
        for name in (None, "", "Growl", "a" * 25, "has space", "bad.wav", 5):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    clean_action({"action": "sound", "name": name})

    def test_tone_defaults_and_ranges(self):
        self.assertEqual(clean_action({"action": "tone"}), {"action": "tone", "hz": 440.0, "ms": 500})
        self.assertEqual(clean_action({"action": "tone", "hz": 880, "ms": 250.0}), {"action": "tone", "hz": 880.0, "ms": 250})
        for body in ({"hz": 10}, {"hz": 9000}, {"ms": 5}, {"ms": 20000}, {"hz": "a"}):
            with self.subTest(body=body):
                with self.assertRaises(ValidationError):
                    clean_action({"action": "tone", **body})

    def test_stop_sound(self):
        self.assertEqual(clean_action({"action": "stop_sound", "name": "ignored"}), {"action": "stop_sound"})

    def test_suggest_sound_name(self):
        self.assertEqual(suggest_sound_name("Big Growl 2.mp3"), "big_growl_2")
        self.assertEqual(suggest_sound_name("/tmp/Scary-Laugh.WAV"), "scary-laugh")
        self.assertEqual(suggest_sound_name("x" * 40 + ".wav"), "x" * 24)
        self.assertEqual(suggest_sound_name("???.ogg"), "clip")


class SceneSoundTests(TestCase):
    def post(self, path, body):
        return self.client.post(path, json.dumps(body), content_type="application/json")

    def test_clean_scene_with_sound_and_volume(self):
        out = clean_scene({"name": "Boo", "state": {"volume": 90}, "action": {"action": "sound", "name": "growl"}})
        self.assertEqual(out["state"], {"volume": 90})
        self.assertEqual(out["action"], {"action": "sound", "name": "growl"})

    def test_scene_crud_with_sound(self):
        resp = self.post("/api/scenes", {"name": "Boo", "action": {"action": "sound", "name": "growl"}, "state": {"volume": 100}})
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["scene"]["action"], {"action": "sound", "name": "growl"})
        bad = self.post("/api/scenes", {"name": "Bad", "action": {"action": "sound", "name": "Nope!"}})
        self.assertEqual(bad.status_code, 400)

    @mock.patch("devices.services.client.action", return_value={"ok": True})
    @mock.patch("devices.services.client.set_state", return_value=STATE)
    def test_apply_scene_sets_volume_then_plays(self, set_state, action):
        make("000000000001", "10.0.0.1")
        scene = Scene.objects.create(name="Boo", state={"volume": 100}, action={"action": "sound", "name": "growl"})
        resp = self.post(f"/api/scenes/{scene.pk}/apply", {})
        self.assertEqual(resp.status_code, 200)
        set_state.assert_called_once_with("10.0.0.1", 80, {"volume": 100})
        action.assert_called_once_with("10.0.0.1", 80, {"action": "sound", "name": "growl"})


class SoundActionFanOutTests(TestCase):
    def post(self, path, body):
        return self.client.post(path, json.dumps(body), content_type="application/json")

    def setUp(self):
        self.speaker = make("000000000001", "10.0.0.1", info={"features": {"speaker": True}})
        self.silent = make("000000000002", "10.0.0.2", info={})
        self.group = Group.objects.create(name="Porch")
        self.group.devices.add(self.speaker, self.silent)

    @mock.patch("devices.services.client.action", return_value={"ok": True})
    def test_sound_to_group_skips_boards_without_speaker(self, action):
        resp = self.post("/api/action", {"target": {"all": True}, "action": "sound", "name": "zap"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.json()["results"]), ["000000000001"])
        action.assert_called_once_with("10.0.0.1", 80, {"action": "sound", "name": "zap"})

    @mock.patch("devices.services.client.action", return_value={"ok": True})
    def test_sound_to_single_silent_board_still_tries(self, action):
        resp = self.post("/api/action", {"target": {"device": "000000000002"}, "action": "stop_sound"})
        self.assertEqual(resp.status_code, 200)
        action.assert_called_once_with("10.0.0.2", 80, {"action": "stop_sound"})

    @mock.patch("devices.services.client.action", return_value={"ok": True})
    def test_blink_still_reaches_everyone(self, action):
        self.post("/api/action", {"target": {"group": self.group.pk}, "action": "blink"})
        self.assertEqual(action.call_count, 2)


class SoundProxyTests(TestCase):
    def setUp(self):
        self.device = make("a1b2c3000091", "10.0.0.5", info=SPEAKER_INFO)
        self.old = make("a1b2c3000099", "10.0.0.9", info={"id": "x"})

    def test_board_without_speaker_404s(self):
        self.assertEqual(self.client.get("/api/devices/a1b2c3000099/sounds").status_code, 404)
        self.assertEqual(self.client.delete("/api/devices/a1b2c3000099/sounds/boo").status_code, 404)

    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    def test_list_stores_listing(self, get_sounds):
        resp = self.client.get("/api/devices/a1b2c3000091/sounds")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sounds"]["clips"][0]["name"], "boo")
        get_sounds.assert_called_once_with("10.0.0.5", 80)
        self.device.refresh_from_db()
        self.assertEqual(self.device.sounds["free_bytes"], 3_000_000)
        self.assertTrue(self.device.online)
        # The device JSON and the scene catalogue pick it up.
        dev = self.client.get("/api/devices/a1b2c3000091").json()["device"]
        self.assertEqual(dev["features"], {"speaker": True, "microphone": True, "battery": True})
        self.assertEqual(dev["sounds"]["builtin"][0], "growl")
        page = self.client.get("/scenes/")
        self.assertEqual(page.context["snapshot"]["sounds"], {"builtin": LISTING["builtin"], "clips": ["boo"]})

    @mock.patch("devices.services.client.get_sounds", side_effect=client.DeviceError("10.0.0.5: ConnectError"))
    def test_list_offline(self, _):
        self.assertEqual(self.client.get("/api/devices/a1b2c3000091/sounds").status_code, 502)

    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    @mock.patch("devices.services.client.delete_sound", return_value={"ok": True})
    def test_delete(self, delete_sound, _):
        resp = self.client.delete("/api/devices/a1b2c3000091/sounds/boo")
        self.assertEqual(resp.status_code, 200)
        delete_sound.assert_called_once_with("10.0.0.5", 80, "boo")
        self.assertEqual(self.client.delete("/api/devices/a1b2c3000091/sounds/Bad%20Name").status_code, 400)

    @mock.patch("devices.services.client.upload_sound")
    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    def test_upload_converts_and_sends(self, _, upload_sound):
        upload_sound.return_value = {**LISTING, "clips": LISTING["clips"] + [{"name": "scream", "bytes": 32044}]}
        wav = make_wav(seconds=1.0, rate=44100, channels=2)
        resp = self.client.post("/api/devices/a1b2c3000091/sounds",
                                {"name": "scream", "file": io.BytesIO(wav)}, format="multipart")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["name"], "scream")
        self.assertEqual([c["name"] for c in resp.json()["sounds"]["clips"]], ["boo", "scream"])
        host, port, name, sent = upload_sound.call_args.args
        self.assertEqual((host, port, name), ("10.0.0.5", 80, "scream"))
        channels, width, rate, frames = wav_layout(sent)
        self.assertEqual((channels, width, rate), (1, 2, 16000))
        self.assertAlmostEqual(frames / rate, 1.0, places=2)
        self.device.refresh_from_db()
        self.assertEqual(len(self.device.sounds["clips"]), 2)

    @mock.patch("devices.services.client.upload_sound")
    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    def test_upload_name_from_filename(self, _, upload_sound):
        upload_sound.return_value = LISTING
        wav = io.BytesIO(make_wav(0.2))
        wav.name = "Evil Laugh.wav"
        resp = self.client.post("/api/devices/a1b2c3000091/sounds", {"file": wav}, format="multipart")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(upload_sound.call_args.args[2], "evil_laugh")

    @mock.patch("devices.services.client.upload_sound")
    @mock.patch("devices.services.client.get_sounds", return_value={**LISTING, "free_bytes": 20000})
    def test_upload_too_big_for_board(self, _, upload_sound):
        wav = io.BytesIO(make_wav(seconds=2.0, rate=16000, channels=1))  # 64 kB after conversion
        wav.name = "long.wav"
        resp = self.client.post("/api/devices/a1b2c3000091/sounds", {"name": "long", "file": wav}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("only has", resp.json()["error"])
        self.assertIn("20.0 kB", resp.json()["error"])
        upload_sound.assert_not_called()

    @mock.patch("devices.services.client.upload_sound")
    @mock.patch("devices.services.client.get_sounds", return_value={**LISTING, "free_bytes": 20000})
    def test_replacing_a_clip_counts_its_space(self, _, upload_sound):
        upload_sound.return_value = LISTING
        wav = io.BytesIO(make_wav(seconds=2.0, rate=16000, channels=1))  # 64 kB; "boo" frees 64 kB more
        wav.name = "boo.wav"
        resp = self.client.post("/api/devices/a1b2c3000091/sounds", {"name": "boo", "file": wav}, format="multipart")
        self.assertEqual(resp.status_code, 201, resp.content)

    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    def test_upload_rejects_bad_names_and_builtins(self, _):
        for name in ("Bad Name", "a" * 25, "growl"):
            with self.subTest(name=name):
                wav = io.BytesIO(make_wav(0.2))
                wav.name = "x.wav"
                resp = self.client.post("/api/devices/a1b2c3000091/sounds", {"name": name, "file": wav}, format="multipart")
                self.assertEqual(resp.status_code, 400)

    def test_upload_needs_a_file(self):
        resp = self.client.post("/api/devices/a1b2c3000091/sounds", {"name": "x"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("choose an audio file", resp.json()["error"])

    @mock.patch("devices.audio.ffmpeg_path", return_value=None)
    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    def test_upload_garbage_without_ffmpeg(self, *_):
        bad = io.BytesIO(b"ID3\x03\x00not really an mp3" * 10)
        bad.name = "song.mp3"
        resp = self.client.post("/api/devices/a1b2c3000091/sounds", {"name": "song", "file": bad}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("WAV", resp.json()["error"])

    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    @mock.patch("devices.services.client.upload_sound", side_effect=client.DeviceError("10.0.0.5: HTTP 400 upload failed (too big?)"))
    def test_board_error_is_502(self, *_):
        wav = io.BytesIO(make_wav(0.2))
        wav.name = "x.wav"
        resp = self.client.post("/api/devices/a1b2c3000091/sounds", {"name": "x", "file": wav}, format="multipart")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("too big", resp.json()["error"])

    @mock.patch("devices.views.client.get_state", return_value=STATE)
    def test_live_state_passthrough(self, get_state):
        resp = self.client.get("/api/devices/a1b2c3000091/state")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["state"]["sound_level"], -61)
        self.device.refresh_from_db()
        self.assertEqual(self.device.last_state, {})  # nothing stored by the meter poll

    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    @mock.patch("devices.services.client.get_state", return_value=STATE)
    @mock.patch("devices.services.client.get_info", return_value=SPEAKER_INFO)
    def test_register_fetches_sounds_for_speaker_boards(self, *_):
        resp = self.client.post("/api/devices", json.dumps({"host": "10.0.0.5"}), content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["device"]["sounds"]["free_bytes"], 3_000_000)
        self.assertEqual(resp.json()["device"]["state"]["battery"]["percent"], 64)


class ConversionTests(TestCase):
    """The pure-Python path; ffmpeg (when installed) is exercised separately below."""

    def test_stereo_44k_to_mono_16k(self):
        out = audio.convert_wav(make_wav(seconds=0.5, rate=44100, channels=2))
        channels, width, rate, frames = wav_layout(out)
        self.assertEqual((channels, width, rate), (1, 2, 16000))
        self.assertAlmostEqual(frames / 16000, 0.5, places=2)
        samples = array.array("h")
        samples.frombytes(out[44:])
        self.assertGreater(max(samples), 15000)  # still has signal (not zeroed by the downmix)

    def test_8_24_32_bit_inputs(self):
        for width in (1, 3, 4):
            with self.subTest(width=width):
                out = audio.convert_wav(make_wav(seconds=0.1, rate=16000, channels=1, width=width))
                self.assertEqual(wav_layout(out)[:3], (1, 2, 16000))
                samples = array.array("h")
                samples.frombytes(out[44:])
                self.assertGreater(max(samples), 10000)

    def test_already_right_passes_through_unchanged_length(self):
        src = make_wav(seconds=0.25, rate=16000, channels=1)
        out = audio.convert_wav(src)
        self.assertEqual(wav_layout(out), wav_layout(src))

    def test_upsample_8k(self):
        out = audio.convert_wav(make_wav(seconds=0.3, rate=8000, channels=1))
        self.assertAlmostEqual(wav_layout(out)[3] / 16000, 0.3, places=2)

    def test_not_a_wav(self):
        for data in (b"", b"RIFF----WAVEjunk", b"\xff\xfb\x90\x00" * 100):
            with self.subTest(data=data[:8]):
                with self.assertRaises(audio.AudioError):
                    audio.convert(data, "x.mp3", use_ffmpeg=False)

    def test_wav_seconds_and_sizes(self):
        self.assertAlmostEqual(audio.wav_seconds(make_wav(1.5, 16000, 1)), 1.5, places=3)
        self.assertEqual(audio.human_size(999), "999 B")
        self.assertEqual(audio.human_size(64_044), "64.0 kB")
        self.assertEqual(audio.human_size(3_400_000), "3.4 MB")

    def test_fix_sizes_patches_streamed_header(self):
        wav = bytearray(make_wav(0.1, 16000, 1))
        wav[4:8] = b"\xff\xff\xff\xff"
        wav[40:44] = b"\xff\xff\xff\xff"
        fixed = audio._fix_sizes(bytes(wav))
        self.assertEqual(wav_layout(fixed)[3], 1600)

    def test_ffmpeg_path_when_installed(self):
        if not audio.ffmpeg_path():
            self.skipTest("ffmpeg not installed")
        out = audio.convert(make_wav(seconds=0.5, rate=22050, channels=2), "x.wav", use_ffmpeg=True)
        channels, width, rate, frames = wav_layout(out)
        self.assertEqual((channels, width, rate), (1, 2, 16000))
        self.assertAlmostEqual(frames / 16000, 0.5, places=1)


class BatteryAndFeaturesTests(TestCase):
    def test_features_default_false_on_old_firmware(self):
        d = make("000000000001", "10.0.0.1", info={"themes": []})
        self.assertEqual(d.features, {"speaker": False, "microphone": False, "battery": False})
        self.assertEqual(self.client.get("/api/devices/000000000001").json()["device"]["sounds"], {})

    def test_features_bad_shape_is_ignored(self):
        d = make("000000000001", "10.0.0.1", info={"features": "yes"})
        self.assertEqual(d.features["speaker"], False)

    def test_board_page_renders_sound_section_markup(self):
        make("a1b2c3000091", "10.0.0.5", info=SPEAKER_INFO, last_state=STATE)
        resp = self.client.get("/boards/a1b2c3000091/")
        self.assertContains(resp, 'id="sound"')
        self.assertContains(resp, 'id="meter"')
        self.assertContains(resp, 'id="board-battery"')
        self.assertEqual(resp.context["snapshot"]["devices"][0]["state"]["battery"]["percent"], 64)

    def test_scene_form_lists_sounds_only_when_a_board_has_a_speaker(self):
        resp = self.client.get("/scenes/")
        self.assertNotContains(resp, 'id="after-sounds"')
        make("a1b2c3000091", "10.0.0.5", info=SPEAKER_INFO, sounds=LISTING)
        resp = self.client.get("/scenes/")
        self.assertContains(resp, 'id="after-sounds"')
        self.assertContains(resp, 'value="sound:boo"')
        self.assertContains(resp, 'name="set_volume"')
