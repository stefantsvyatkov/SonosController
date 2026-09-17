"""Seek guards, metadata fallbacks and immediate settings changes."""
import unittest
from test_sonos_backend import Player, Group, backend
from support import load

media = load("media")
describe, source_name, seconds = media.describe, media.source_name, media.seconds
Settings = load("settings").Settings


class MediaTests(unittest.TestCase):
    def test_sources_and_unknown_metadata(self):
        for uri, source in (("x-sonos-vli:test,spotify:abc", "Spotify"),
                            ("x-sonos-vli:bluetooth", "Bluetooth"),
                            ("x-sonos-htastream:test", "TV"),
                            ("x-rincon-stream:test", "Line-in"),
                            ("x-rincon-mp3radio:test", "Internet radio"),
                            ("x-sonos-airplay:test", "AirPlay")):
            self.assertEqual(source_name(uri), source)
        self.assertIn("Track details unavailable", describe({"uri": "x-sonos-vli:test"}, "PLAYING"))

    def test_radio_metadata_and_invalid_xml(self):
        xml = '<item><title>Station</title><streamContent>Artist - Song</streamContent></item>'
        self.assertIn("Station, Artist - Song", describe({"metadata": xml}, "PLAYING"))
        self.assertIn("unavailable", describe({"metadata": "NOT_IMPLEMENTED"}, "STOPPED"))
        self.assertIsNone(seconds("NOT_IMPLEMENTED"))
        self.assertIsNone(seconds("0:99:00"))

    def test_seek_and_limits(self):
        p = Player("a", "Room")
        Group(p)
        p.actions = "Play,Seek"
        p.get_current_track_info = lambda: {"position": "0:00:05", "duration": "0:01:00"}
        positions = []
        p.seek = lambda position: positions.append(position)
        c = backend.Controller(lambda **kw: [p])
        c.execute("seekBack")
        c.execute("seekForward")
        self.assertEqual(positions, ["0:00:00", "0:00:15"])
        p.actions = "Play"
        self.assertIn("not available", c.execute("seekForward"))
        self.assertEqual(len(positions), 2)

    def test_live_stream_never_seeks(self):
        p = Player("a", "Room")
        Group(p)
        p.actions = "Seek"
        p.get_current_track_info = lambda: {"position": "0:03:00", "duration": "0:00:00"}
        p.seek = lambda **kw: self.fail("Must not seek a stream without duration")
        self.assertIn("not available", backend.Controller(lambda **kw: [p]).execute("seekForward"))

    def test_spotify_dlna_time_seek(self):
        p = Player("a", "Room")
        Group(p)
        p.actions = "Set, Stop, Pause, Play, X_DLNA_SeekTime, Next, Previous"
        p.get_current_track_info = lambda: {"uri": "x-sonos-vli:spotify:session", "position": "0:01:00", "duration": "0:03:00"}
        positions = []
        p.seek = lambda position: positions.append(position)
        backend.Controller(lambda **kw: [p]).execute("seekForward")
        self.assertEqual(positions, ["0:01:10"])


class SettingsTests(unittest.TestCase):
    def test_immediate_numeric_and_boolean_updates(self):
        p = Player("a", "Room")
        Group(p)
        p.bass, p.treble, p.loudness, p.status_light, p.buttons_enabled = 10, 0, True, False, True
        settings = Settings(backend.Controller(lambda **kw: [p]))
        settings.adjust(1)
        self.assertEqual(p.bass, 10)
        settings.adjust(-1)
        self.assertEqual(p.bass, 9)
        settings.move(1)
        settings.move(1)
        settings.adjust(-1)
        self.assertFalse(p.loudness)
        settings.adjust(1)
        self.assertTrue(p.loudness)

    def test_unsupported_settings_not_listed(self):
        p = Player("a", "Room")
        Group(p)
        p.status_light = True
        settings = Settings(backend.Controller(lambda **kw: [p]))
        self.assertEqual({option.key for _, option in settings.items}, {"status_light", "volume", "mute"})
        self.assertIn("Status light", settings.describe())

    def model(self, name, **flags):
        p = Player("a", "Room")
        Group(p)
        p.get_speaker_info = lambda: {"model_name": name}
        for key, value in flags.items():
            setattr(p, key, value)
        return p

    def test_soundbar_without_surround_or_sub_hides_controls(self):
        p = self.model("Sonos Beam", is_soundbar=True, has_satellites=False, has_subwoofer=False,
                       night_mode=False, dialog_mode=True, audio_delay=0, surround_enabled=True, sub_enabled=True)
        settings = Settings(backend.Controller(lambda **kw: [p]))
        keys = {o.key for _, o in settings.items}
        self.assertTrue({"night_mode", "dialog_mode", "audio_delay"} <= keys)
        self.assertFalse({"surround_enabled", "sub_enabled"} & keys)
        self.assertIn("Sonos Beam", settings.describe())

    def test_arc_ultra_with_satellites(self):
        p = self.model("Sonos Arc Ultra", is_soundbar=True, is_arc_ultra_soundbar=True,
                       has_satellites=True, _ht_sat_chan_map="rear:LR;rear2:RR;sub:SW",
                       has_subwoofer=True, surround_enabled=True, sub_enabled=True,
                       sub_gain=0, speech_enhance_enabled=True, dialog_mode=True)
        keys = {o.key for _, o in Settings(backend.Controller(lambda **kw: [p])).items}
        self.assertTrue({"sub_gain", "sub_enabled", "surround_enabled", "speech_enhance_enabled"} <= keys)
        self.assertNotIn("dialog_mode", keys)

    def test_amp_crossover_and_calibration_none(self):
        p = self.model("Sonos Amp", sub_crossover=80, trueplay=None)
        settings = Settings(backend.Controller(lambda **kw: [p]))
        keys = {o.key for _, o in settings.items}
        self.assertIn("sub_crossover", keys)
        self.assertNotIn("trueplay", keys)

    def test_fixed_output_only_supported_devices(self):
        p = self.model("Sonos Port", supports_fixed_volume=True, fixed_volume=False)
        self.assertIn("fixed_volume", {o.key for _, o in Settings(backend.Controller(lambda **kw: [p])).items})
        p.get_speaker_info = lambda: {"model_name": "Sonos Era 100"}
        self.assertNotIn("fixed_volume", {o.key for _, o in Settings(backend.Controller(lambda **kw: [p])).items})

    def test_balance_and_timer_conversion(self):
        p = self.model("Sonos Port", balance=(100, 100), is_coordinator=True)
        p.get_current_media_info = lambda: {"uri": "x-rincon-queue:a"}
        p.get_sleep_timer = lambda: None
        saved = []
        p.set_sleep_timer = saved.append
        settings = Settings(backend.Controller(lambda **kw: [p]))
        settings.index = next(i for i, (_, o) in enumerate(settings.items) if o.key == "balance")
        settings.adjust(1)
        self.assertEqual(p.balance, (99, 100))
        self.assertNotIn("sleep_timer", {o.key for _, o in settings.items})
        self.assertEqual(saved, [])
