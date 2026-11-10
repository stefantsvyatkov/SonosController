"""Seek guards, metadata fallbacks and immediate settings changes."""
import unittest
from test_sonos_backend import Player, Group, backend
from support import load

media = load("media")
describe, source_name, seconds = media.describe, media.source_name, media.seconds
readable = load("util").readable
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
        self.assertIn("Track information unavailable", describe({"uri": "x-sonos-vli:test"}, "PLAYING"))

    def test_radio_metadata_and_invalid_xml(self):
        xml = '<item><title>Station</title><streamContent>Artist - Song</streamContent></item>'
        self.assertIn("Station, Artist - Song", describe({"metadata": xml}, "PLAYING"))
        self.assertIn("unavailable", describe({"metadata": "NOT_IMPLEMENTED"}, "STOPPED"))
        self.assertIsNone(seconds("NOT_IMPLEMENTED"))
        self.assertIsNone(seconds("0:99:00"))

    def test_a_playing_station_is_named_without_its_stream_file(self):
        """What two of the owner's speakers reported on 10 November 2026."""
        def didl(body):
            return ('<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
                    'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
                    f'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"><item>{body}</item></DIDL-Lite>')

        def station(name, uri):
            return {"CurrentURI": uri, "CurrentURIMetaData": didl(f"<dc:title>{name}</dc:title>")}

        stream = "aac://https://25703.live.streamtheworld.com:443/RADIO_ENERGYAAC_H.aac?dist=TUNEIN"
        track = {"title": "DIGIDI DIGIDI", "artist": "DJ.ILHAM", "uri": stream, "metadata": didl(
            "<r:streamContent>DJ.ILHAM - DIGIDI DIGIDI</r:streamContent>"
            "<dc:title>RADIO_ENERGYAAC_H.aac?dist=TUNEIN</dc:title>")}
        self.assertEqual(
            describe(track, "playing", station("Radio Energy", "x-sonosapi-stream:tunein%3a17501?sid=303")),
            "Internet radio, Radio Energy, DIGIDI DIGIDI, DJ.ILHAM, playing")
        stream = "hls-radio://https://bss1.neterra.tv/thevoicefm/thevoicefm.m3u8"
        track = {"title": "", "artist": "", "uri": stream, "metadata": didl(
            "<r:streamContent></r:streamContent><dc:title>thevoicefm.m3u8</dc:title>")}
        self.assertEqual(
            describe(track, "playing", station("The Voice Radio", "x-sonosapi-stream:r%3a414762?sid=268")),
            "Internet radio, The Voice Radio, playing")

    def test_what_a_speaker_reports_while_connecting_or_mangled_is_cleaned(self):
        """The owner's Play, given a stream named in UTF-8 and one in Windows-1251."""
        mangled = "Тестово радио  с   интервали".encode("utf-8").decode("latin-1")
        stream = "x-rincon-mp3radio://http://192.0.2.1/stream.mp3"
        media = {"CurrentURI": stream, "CurrentURIMetaData":
                 '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/"><item>'
                 '<dc:title>Тестово радио с интервали</dc:title></item></DIDL-Lite>'}
        self.assertEqual(describe({"title": mangled, "uri": stream}, "playing", media),
                         "Internet radio, Тестово радио с интервали, playing")
        self.assertEqual(describe({"title": "ZPSTR_BUFFERING", "uri": stream}, "transitioning", media),
                         "Internet radio, Тестово радио с интервали, transitioning")
        self.assertEqual(readable("Радио Тест 1251".encode("cp1251").decode("latin-1")), "Радио Тест 1251")
        for text in ("Café FM", "Müller Radio", "Radio 1", "Радио 1"):
            self.assertEqual(readable(text), text)

    def test_a_queue_track_keeps_its_own_source_and_title(self):
        track = {"title": "01-silence.mp3", "uri": "http://192.168.0.2:4100/a1b2/01-silence.mp3"}
        self.assertEqual(describe(track, "playing", {"CurrentURI": "x-rincon-queue:RINCON_1#0"}),
                         "Network audio, 01-silence.mp3, playing")

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
        self.assertIn("unavailable", c.execute("seekForward"))
        self.assertEqual(len(positions), 2)

    def test_live_stream_never_seeks(self):
        p = Player("a", "Room")
        Group(p)
        p.actions = "Seek"
        p.get_current_track_info = lambda: {"position": "0:03:00", "duration": "0:00:00"}
        p.seek = lambda **kw: self.fail("Must not seek a stream without duration")
        self.assertIn("unavailable", backend.Controller(lambda **kw: [p]).execute("seekForward"))

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
        p = self.model("Sonos Arc Ultra", is_soundbar=True, is_ultra_soundbar=True,
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
