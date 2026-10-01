"""Theme sounds and the voice of the skull: validation, the voice endpoints, effects and the fake board."""
import io
import json
import shutil
import sys
import wave
from unittest import mock

from django.test import TestCase

from devices import audio, client, services
from devices.models import Device, Group
from devices.validation import ValidationError, clean_effect, clean_scene, clean_state, clean_url

from .test_sound import LISTING, SPEAKER_INFO, STATE, make, make_wav, wav_layout

SKULL_STATE = {**STATE, "theme_sounds": True, "theme_sound": "growl"}


class ThemeSoundValidationTests(TestCase):
    def test_theme_sound_fields(self):
        self.assertEqual(clean_state({"theme_sounds": False}), {"theme_sounds": False})
        self.assertEqual(clean_state({"theme_sound": "growl"}), {"theme_sound": "growl"})
        self.assertEqual(clean_state({"theme_sound": None}), {"theme_sound": None})
        for state in ({"theme_sounds": 1}, {"theme_sounds": "yes"}, {"theme_sound": "Not Valid"}, {"theme_sound": 3}):
            with self.subTest(state=state):
                with self.assertRaises(ValidationError):
                    clean_state(state)

    def test_scene_can_pair_a_theme_with_a_sound(self):
        out = clean_scene({"name": "Haunt", "state": {"theme": "ghost", "theme_sound": "whisper", "theme_sounds": True}})
        self.assertEqual(out["state"], {"theme": "ghost", "theme_sound": "whisper", "theme_sounds": True})

    def test_effect_and_url(self):
        self.assertEqual(clean_effect(None), "natural")
        self.assertEqual(clean_effect(""), "natural")
        self.assertEqual(clean_effect("demon"), "demon")
        with self.assertRaises(ValidationError):
            clean_effect("chipmunk")
        self.assertEqual(clean_url(" https://example.com/a.mp3 "), "https://example.com/a.mp3")
        for url in (None, "", "ftp://x/y", "file:///etc/passwd", "example.com/a.mp3", "https://" + "x" * 2100):
            with self.subTest(url=url):
                with self.assertRaises(ValidationError):
                    clean_url(url)

    @mock.patch("devices.services.client.set_state", return_value=SKULL_STATE)
    def test_state_endpoint_passes_pairing_through(self, set_state):
        make("000000000001", "10.0.0.1", info=SPEAKER_INFO)
        resp = self.client.post("/api/state", json.dumps({"target": {"all": True}, "state": {"theme_sound": None, "theme_sounds": True}}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        set_state.assert_called_once_with("10.0.0.1", 80, {"theme_sound": None, "theme_sounds": True})


class VoiceEndpointTests(TestCase):
    def setUp(self):
        self.speaker = make("a1b2c3000091", "10.0.0.5", name="Porch", info=SPEAKER_INFO, sounds=LISTING)
        self.silent = make("a1b2c3000099", "10.0.0.9", name="Old", info={"id": "x"})
        self.group = Group.objects.create(name="Front")
        self.group.devices.add(self.speaker, self.silent)

    def post_voice(self, target, effect="natural", data=None, name="voice.wav"):
        f = io.BytesIO(data if data is not None else make_wav(0.5, 16000, 1))
        f.name = name
        body = {"file": f, "target": json.dumps(target)}
        if effect is not None:
            body["effect"] = effect
        return self.client.post("/api/voice", body, format="multipart")

    @mock.patch("devices.services.client.action", return_value={"ok": True})
    @mock.patch("devices.services.client.upload_sound", return_value={**LISTING, "clips": [{"name": "voice", "bytes": 16044}]})
    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    def test_speak_uploads_voice_clip_and_plays(self, _, upload_sound, action):
        resp = self.post_voice({"device": "a1b2c3000091"})
        self.assertEqual(resp.status_code, 200, resp.content)
        result = resp.json()["results"]["a1b2c3000091"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["playing"], "voice")
        self.assertEqual(result["effect"], "natural")
        self.assertAlmostEqual(result["seconds"], 0.5, places=1)
        host, port, name, sent = upload_sound.call_args.args
        self.assertEqual((host, port, name), ("10.0.0.5", 80, "voice"))
        self.assertEqual(wav_layout(sent)[:3], (1, 2, 16000))
        action.assert_called_once_with("10.0.0.5", 80, {"action": "sound", "name": "voice"})
        self.speaker.refresh_from_db()
        self.assertEqual([c["name"] for c in self.speaker.sounds["clips"]], ["voice"])

    @mock.patch("devices.services.client.action", return_value={"ok": True})
    @mock.patch("devices.services.client.upload_sound", return_value=LISTING)
    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    def test_speak_to_group_skips_silent_boards(self, *_):
        resp = self.post_voice({"group": self.group.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.json()["results"]), ["a1b2c3000091"])

    def test_speak_to_a_lone_silent_board_says_so(self):
        resp = self.post_voice({"device": "a1b2c3000099"})
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["results"]["a1b2c3000099"]
        self.assertFalse(result["ok"])
        self.assertIn("no speaker", result["error"])

    @mock.patch("devices.services.audio.convert", return_value=make_wav(0.5, 16000, 1))
    @mock.patch("devices.services.client.action", return_value={"ok": True})
    @mock.patch("devices.services.client.upload_sound", return_value=LISTING)
    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    def test_effect_is_passed_to_the_converter(self, _, __, ___, convert):
        resp = self.post_voice({"device": "a1b2c3000091"}, effect="demon", data=b"\x1aE\xdf\xa3 fake webm", name="voice.webm")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["effect"], "demon")
        self.assertEqual(convert.call_args.args[:2], (b"\x1aE\xdf\xa3 fake webm", "voice.webm"))
        self.assertEqual(convert.call_args.kwargs, {"effect": "demon"})

    def test_bad_requests(self):
        self.assertEqual(self.client.post("/api/voice", {"target": "{}"}).status_code, 400)  # no file
        self.assertEqual(self.post_voice({"device": "a1b2c3000091"}, effect="chipmunk").status_code, 400)
        self.assertEqual(self.post_voice({"device": "nope"}).status_code, 400)
        resp = self.client.post("/api/voice", {"file": io.BytesIO(b"x"), "target": "not json"}, format="multipart")
        self.assertEqual(resp.status_code, 400)

    @mock.patch("devices.audio.ffmpeg_path", return_value=None)
    def test_effects_need_ffmpeg(self, _):
        resp = self.post_voice({"device": "a1b2c3000091"}, effect="robot")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("ffmpeg", resp.json()["error"])

    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    def test_garbage_recording_is_a_400(self, _):
        with mock.patch("devices.audio.ffmpeg_path", return_value=None):
            resp = self.post_voice({"device": "a1b2c3000091"}, data=b"not audio at all", name="voice.webm")
        self.assertEqual(resp.status_code, 400)

    @mock.patch("devices.services.client.upload_sound")
    @mock.patch("devices.services.client.get_sounds", return_value={**LISTING, "free_bytes": 8000})
    def test_too_long_for_the_board_is_reported_per_board(self, _, upload_sound):
        resp = self.post_voice({"device": "a1b2c3000091"}, data=make_wav(2.0, 16000, 1))
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["results"]["a1b2c3000091"]
        self.assertFalse(result["ok"])
        self.assertIn("won't fit", result["error"])
        upload_sound.assert_not_called()

    @mock.patch("devices.services.client.action", return_value={"ok": True})
    @mock.patch("devices.services.client.upload_sound", return_value=LISTING)
    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    @mock.patch("devices.services.audio.fetch_url", return_value=(make_wav(0.3, 16000, 1), "boo.wav"))
    def test_play_from_url(self, fetch_url, _, upload_sound, action):
        resp = self.client.post("/api/voice/url", json.dumps({"target": {"all": True}, "url": "https://example.com/boo.wav", "effect": "ghost"}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)
        fetch_url.assert_called_once()
        self.assertEqual(fetch_url.call_args.args[0], "https://example.com/boo.wav")
        self.assertEqual(list(resp.json()["results"]), ["a1b2c3000091"])
        self.assertEqual(upload_sound.call_args.args[2], "voice")
        action.assert_called_once_with("10.0.0.5", 80, {"action": "sound", "name": "voice"})

    @mock.patch("devices.services.audio.fetch_url", side_effect=audio.AudioError("that address answered HTTP 404"))
    def test_play_from_url_fetch_failure(self, _):
        resp = self.client.post("/api/voice/url", json.dumps({"target": {"all": True}, "url": "https://example.com/x.mp3"}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("404", resp.json()["error"])
        bad = self.client.post("/api/voice/url", json.dumps({"target": {"all": True}, "url": "ftp://x"}), content_type="application/json")
        self.assertEqual(bad.status_code, 400)

    @mock.patch("devices.services.client.get_sounds", return_value=LISTING)
    @mock.patch("devices.services.client.upload_sound", side_effect=client.DeviceError("10.0.0.5: HTTP 400 upload failed"))
    def test_board_error_lands_in_results(self, *_):
        resp = self.post_voice({"device": "a1b2c3000091"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("upload failed", resp.json()["results"]["a1b2c3000091"]["error"])

    def test_pages_carry_voice_markup(self):
        resp = self.client.get("/boards/a1b2c3000091/")
        self.assertContains(resp, 'id="voice"')
        self.assertContains(resp, 'id="pairing"')
        self.assertContains(resp, 'id="theme-sound"')
        self.assertEqual([e["id"] for e in resp.context["snapshot"]["voice_effects"]], ["natural", "demon", "ghost", "robot"])
        self.assertContains(self.client.get("/puppeteer/"), 'id="voice"')


class VoiceEffectTests(TestCase):
    def test_every_effect_has_a_chain_and_label(self):
        from devices.validation import VOICE_EFFECTS

        self.assertEqual(list(audio.EFFECTS), VOICE_EFFECTS)
        for e in VOICE_EFFECTS:
            self.assertIn(e, audio.EFFECT_LABELS)
            self.assertTrue(audio.EFFECTS[e].startswith("aresample=16000,"))
            self.assertIn("loudnorm", audio.EFFECTS[e])

    def test_natural_without_ffmpeg_falls_back_to_plain_wav(self):
        out = audio.convert(make_wav(0.2, 44100, 2), "x.wav", use_ffmpeg=False, effect="natural")
        self.assertEqual(wav_layout(out)[:3], (1, 2, 16000))
        with self.assertRaises(audio.AudioError):
            audio.convert(make_wav(0.2), "x.wav", use_ffmpeg=False, effect="demon")
        with self.assertRaises(audio.AudioError):
            audio.convert(make_wav(0.2), "x.wav", effect="nope")

    def test_effects_with_real_ffmpeg(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg not installed")
        src = make_wav(seconds=1.0, rate=44100, channels=2, hz=220.0)
        for effect in audio.EFFECTS:
            with self.subTest(effect=effect):
                out = audio.convert(src, "voice.wav", use_ffmpeg=True, effect=effect)
                channels, width, rate, frames = wav_layout(out)
                self.assertEqual((channels, width, rate), (1, 2, 16000))
                # Echo tails make demon/ghost a touch longer; nothing should be shorter than the source.
                self.assertGreaterEqual(frames / rate, 0.95)
                self.assertLess(frames / rate, 1.6)
                with wave.open(io.BytesIO(out)) as w:
                    pcm = w.readframes(frames)
                peak = max(abs(int.from_bytes(pcm[i:i + 2], "little", signed=True)) for i in range(0, len(pcm), 2))
                self.assertGreater(peak, 3000, "loudnorm should leave a clearly audible signal")


class FakeBoardThemeSoundTests(TestCase):
    """The simulator honours the theme-sound contract (without building the renderer)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        import fake_device

        cls.fake = fake_device

    def board(self, **kw):
        return self.fake.Board(None, "a1b2c3000001", "sauron", "Fake", **kw)

    def test_info_defaults_and_state_fields(self):
        b = self.board()
        self.assertEqual(next(t["sound"] for t in b.themes if t["id"] == "sauron"), "growl")
        s = b.full_state()
        self.assertTrue(s["theme_sounds"])
        self.assertEqual(s["theme_sound"], "growl")

    def test_pairing_is_per_theme_and_validated(self):
        b = self.board()
        self.assertIsNone(b.apply_state({"theme_sound": "zap"}))
        self.assertEqual(b.full_state()["theme_sound"], "zap")
        self.assertEqual(b.apply_state({"theme_sound": "nope"}), "unknown sound")
        self.assertEqual(b.apply_state({"theme_sounds": "yes"}), "theme_sounds must be boolean")
        self.assertIsNone(b.apply_state({"theme_sound": None}))
        self.assertIsNone(b.full_state()["theme_sound"])

    def test_startle_plays_the_paired_sound_when_enabled(self):
        b = self.board()
        b.apply_action({"action": "startle"})
        self.assertEqual(b.full_state()["playing"], "growl")
        b.playing = None
        b.apply_state({"theme_sounds": False})
        b.apply_action({"action": "startle"})
        self.assertIsNone(b.full_state()["playing"])

    def test_legacy_and_silent_boards_have_no_pairing(self):
        legacy = self.board(legacy=True)
        self.assertNotIn("theme_sounds", legacy.full_state())
        self.assertEqual(legacy.apply_state({"theme_sounds": True}), "unknown field theme_sounds")
        silent = self.board(speaker=False)
        self.assertNotIn("theme_sound", silent.full_state())
        self.assertNotIn("sound", silent.themes[0])
