"""Every kind of Sonos product, in the setups people actually have.

``product_household`` holds soundbars with and without rear speakers and
subwoofers, a stereo pair, portables (one asleep), a Port at fixed output, an
Amp, an Amp Multi serving three rooms, IKEA's speaker and an S1 speaker.  The
simulated speakers decide their capabilities from the model name the way SoCo
does, and ``SoCoDrift`` checks that the names they answer to are SoCo's own, so
a SoCo update that renames one fails here rather than on a user's soundbar.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import Session
from harness import sonos as simulator
from harness.sonos import product_household
from support import load

backend = load("backend")
settings_module = load("settings")

ROOMS = sorted(p.player_name for p in product_household().visible() if p.player_name != "Garage")


def labels(house, name):
    """The settings Control+S offers for one room, as (label) strings."""
    room = house.find(name)
    controller = backend.Controller(lambda **kw: [p for p in house.visible() if p.online],
                                    selected=room.uid)
    return [option.label for _room, option in settings_module.Settings(controller).items]


class Rooms(unittest.TestCase):
    def setUp(self):
        self.house = product_household()
        self.session = Session(household=self.house)
        self.addCleanup(self.session.close)
        self.opened = self.session.press("kb:NVDA+shift+control+s")

    def walk(self):
        """Every room the arrows reach, once round."""
        heard = []
        for _ in range(len(self.house.players) + 1):
            line = self.session.press("kb:rightArrow")[-1]
            room = line.split(",")[0]
            if room in heard:
                break
            heard.append(room)
        return heard

    def test_only_rooms_are_listed(self):
        # Rear speakers, subwoofers and the second speaker of a pair are part of
        # a room, not rooms; the sleeping Roam SL is left out, not waited for.
        self.assertEqual(sorted(self.walk()), ROOMS)

    def test_amp_multi_rooms_share_one_address(self):
        zones = [self.house.find(name) for name in ("Zone 1", "Zone 2", "Zone 3")]
        self.assertEqual(len({z.ip_address for z in zones}), 1)
        self.assertEqual(len({z.port for z in zones}), 3)
        self.assertTrue({"Zone 1", "Zone 2", "Zone 3"} <= set(self.walk()))

    def test_volume_works_everywhere_but_fixed_output(self):
        for name in ROOMS:
            self.session.select_room(name)
            answer = self.session.press("kb:upArrow")[-1]
            if name == "Hi-Fi":
                self.assertEqual(answer, "Fixed volume on Hi-Fi.")
            else:
                self.assertRegex(answer, r"^\d+ percent$", name)

    def test_battery_only_on_portables(self):
        expected = {"Patio": "Patio, battery 82 percent, power source sonos charging ring",
                    "Garden": "Garden, battery 82 percent, power source battery",
                    "Beach Bag": "Beach Bag, battery 46 percent, power source battery",
                    "Balcony": "Balcony, battery 82 percent, power source battery"}
        for name in ROOMS:
            self.session.select_room(name)
            self.assertEqual(self.session.press("kb:b")[-1],
                             expected.get(name, "Battery not available."), name)

    def test_what_is_playing_names_the_source(self):
        self.session.select_room("Living Room")
        self.assertEqual(self.session.press("kb:p")[-1], "TV, playing")
        self.session.select_room("Kitchen")
        self.assertTrue(self.session.press("kb:p")[-1].startswith("Internet radio"))

    def test_settings_open_in_every_room(self):
        for name in ROOMS:
            self.session.select_room(name)
            first = self.session.press("kb:control+s")[-1]
            self.assertTrue(first.startswith("Settings. " + name), first)
            self.session.press("kb:escape")

    def test_group_and_break_up_a_home_theater(self):
        self.session.select_room("Living Room")
        self.session.press("kb:g")
        self.session.select_room("Kitchen")
        self.session.press("kb:space")
        self.assertEqual(self.session.press("kb:enter")[-1], "Playing rooms: Kitchen, Living Room")
        bar = self.house.find("Living Room")
        self.assertEqual(self.house.find("Kitchen")._coordinator, bar.uid)
        self.assertEqual(self.session.press("kb:shift+g")[-1],
                         "Group broken up. Living Room keeps playing.")
        # The soundbar keeps its rear speakers and subwoofer.
        self.assertEqual(sum(p._coordinator == bar.uid for p in self.house.players), 4)
        self.assertEqual(self.house.find("Kitchen").playback.state, "STOPPED")
        self.assertEqual(bar.playback.state, "PLAYING")

    def test_break_up_names_a_room_that_is_not_grouped(self):
        self.session.select_room("Study")
        self.assertEqual(self.session.press("kb:shift+g")[-1], "Study is not grouped.")

    def test_transfer_from_a_portable(self):
        patio = self.house.find("Patio")
        patio.playback.state = "PLAYING"
        patio.playback.uri = "x-rincon-queue:%s#0" % patio.uid
        patio.playback.title = "Song"
        self.session.select_room("Patio")
        self.session.press("kb:t")
        self.session.press("kb:space")
        self.assertIn("Audio transferred to", self.session.speech.last())

    def test_a_portable_falling_asleep_does_not_stop_the_others(self):
        self.session.select_room("Kitchen")
        self.house.find("Patio").online = False
        for name in ("Study", "Bedroom", "Zone 2"):
            self.assertIn(name, self.session.select_room(name))
            self.assertRegex(self.session.press("kb:upArrow")[-1], r"^\d+ percent$")

    def test_alarm_rooms_are_the_rooms(self):
        heard = []

        def read(dialog):
            heard.extend(dialog.focus_on("Room").choices)
            return False

        self.session.press("kb:a")
        self.session.on_dialog(read)
        self.session.press("kb:n")
        self.assertEqual(sorted(heard), ROOMS)


class Settings(unittest.TestCase):
    def setUp(self):
        self.house = product_household()

    def test_ultra_soundbars_offer_their_speech_enhancement(self):
        for name in ("Living Room", "TV Room"):
            offered = labels(self.house, name)
            self.assertEqual(offered.count("Speech enhancement"), 1, name)
            self.assertIn("Night mode", offered)
        bar = self.house.find("TV Room")
        controller = backend.Controller(lambda **kw: [bar], selected=bar.uid)
        keys = {option.key for _room, option in settings_module.Settings(controller).items}
        self.assertIn("speech_enhance_enabled", keys)
        self.assertNotIn("dialog_mode", keys)

    def test_home_theater_with_rears_and_sub(self):
        offered = labels(self.house, "Living Room")
        for label in ("Surround speakers", "Surround TV level", "Surround music level",
                      "Subwoofer", "Subwoofer level", "TV dialog sync"):
            self.assertIn(label, offered)
        self.assertNotIn("Subwoofer crossover Hz", offered)

    def test_soundbar_alone_has_no_surround_or_sub(self):
        offered = labels(self.house, "Guest Room")
        self.assertIn("Speech enhancement", offered)
        self.assertFalse({"Surround speakers", "Subwoofer"} & set(offered))

    def test_soundbar_with_sub_only(self):
        offered = labels(self.house, "Den")
        self.assertIn("Subwoofer level", offered)
        self.assertNotIn("Surround speakers", offered)

    def test_stereo_pair_with_sub(self):
        offered = labels(self.house, "Bedroom")
        self.assertIn("Subwoofer level", offered)
        self.assertNotIn("Night mode", offered)

    def test_speakers_and_portables_have_the_common_settings(self):
        for name in ("Kitchen", "Study", "Lobby", "Bathroom", "Bookshelf", "Attic",
                     "Patio", "Garden", "Beach Bag", "Balcony", "Zone 1"):
            offered = labels(self.house, name)
            self.assertTrue({"Bass", "Treble", "Loudness", "Balance"} <= set(offered), name)
            self.assertFalse({"Night mode", "Subwoofer", "Fixed line output"} & set(offered), name)

    def test_amp_offers_crossover_and_tv_settings(self):
        offered = labels(self.house, "Terrace")
        for label in ("Subwoofer crossover Hz", "Subwoofer level", "Night mode"):
            self.assertIn(label, offered)

    def test_port_and_connect_offer_fixed_output_without_touch_controls(self):
        for name in ("Hi-Fi", "Record Player"):
            offered = labels(self.house, name)
            self.assertIn("Fixed line output", offered)
            self.assertNotIn("Touch controls", offered)


class SoCoDrift(unittest.TestCase):
    """The simulator must answer to SoCo's names, or the tests prove nothing."""

    @classmethod
    def setUpClass(cls):
        soco = load("vendors").soco()
        cls.core = sys.modules[soco.__name__ + ".core"]
        cls.groups = sys.modules[soco.__name__ + ".groups"]

    def test_model_lists_match_soco(self):
        self.assertEqual(set(simulator.SOUNDBARS), set(self.core.SOUNDBARS))
        self.assertEqual(set(simulator.ULTRA_SOUNDBARS), set(self.core.ULTRA_SOUNDBARS))

    def test_settings_are_soco_properties(self):
        for key in [option.key for option in settings_module.OPTIONS] + ["shuffle", "repeat", "cross_fade"]:
            self.assertTrue(hasattr(self.core.SoCo, key), key)

    def test_capabilities_and_calls_are_socos(self):
        for name in ("is_soundbar", "is_ultra_soundbar", "has_satellites", "has_subwoofer",
                     "is_coordinator", "supports_fixed_volume", "fixed_volume", "get_battery_info",
                     "get_speaker_info", "join", "unjoin", "group", "all_zones", "play_uri",
                     "add_to_queue", "play_from_queue", "get_sleep_timer", "set_sleep_timer",
                     "get_current_transport_info", "get_current_track_info",
                     "get_current_media_info", "seek", "play", "pause", "stop", "next",
                     "previous", "clear_queue", "add_multiple_to_queue", "zone_group_state",
                     "player_name", "uid", "is_visible", "ip_address", "music_library"):
            self.assertTrue(hasattr(self.core.SoCo, name) or name in ("player_name", "uid", "is_visible",
                                                                     "ip_address", "music_library"),
                            name)
            self.assertTrue(hasattr(simulator.SimPlayer, name) or name in simulator.SETTING_KEYS or name in (
                "player_name", "uid", "is_visible", "ip_address", "music_library", "zone_group_state"),
                name)
        for name in ("set_relative_volume", "volume", "mute"):
            self.assertTrue(hasattr(self.groups.ZoneGroup, name), name)
        group = self.groups.ZoneGroup("RINCON_1", coordinator=None, members=set())
        self.assertTrue(hasattr(group, "coordinator") and hasattr(group, "members"))

    def test_multi_port_players(self):
        player = self.core.SoCo("192.0.2.10", 1410)
        self.assertEqual(player.port, 1410)


if __name__ == "__main__":
    unittest.main()


class Discovery(unittest.TestCase):
    discovery = load("discovery")

    def test_saved_address_finds_every_amp_multi_room(self):
        house = product_household()
        zone = house.find("Zone 1")
        session = Session(household=house, addresses=[zone.ip_address])
        self.addCleanup(session.close)
        session.discovery.discover_rooms = lambda *a, **k: self.fail("SSDP must not be needed")
        session.press("kb:NVDA+shift+control+s")
        heard = set()
        for _ in range(len(house.players)):
            heard.add(session.press("kb:rightArrow")[-1].split(",")[0])
        self.assertTrue({"Zone 1", "Zone 2", "Zone 3", "Living Room"} <= heard)
        self.assertNotIn("Garage", heard)

    def test_ssdp_reply_keeps_the_players_port(self):
        reply = (b"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age = 1800\r\n"
                 b"LOCATION: http://192.168.1.40:1410/xml/device_description.xml\r\n"
                 b"SERVER: Linux UPnP/1.0 Sonos/90.1-62170 (ZPS42)\r\n\r\n")
        self.assertEqual(self.discovery._ssdp_port(reply), 1410)
        self.assertEqual(self.discovery._ssdp_port(reply.replace(b":1410", b"")), 1400)
        self.assertEqual(self.discovery._ssdp_port(b"HTTP/1.1 200 OK\r\nSERVER: Sonos\r\n\r\n"), 1400)


class Battery(unittest.TestCase):
    def test_a_silent_portable_is_reported_not_skipped(self):
        house = product_household()
        house.find("Patio").online = False
        patio = house.find("Patio")
        controller = backend.Controller(lambda **kw: [patio], selected=patio.uid)
        controller.rooms = [patio]
        patio_group = simulator.SimGroup(patio, [patio])
        controller.current = lambda: patio_group
        self.assertEqual(controller.execute("battery"), "Battery could not be read.")


class SilentMember(unittest.TestCase):
    def test_a_grouped_portable_that_sleeps_is_named_not_fatal(self):
        house = product_household()
        kitchen, patio = house.find("Kitchen"), house.find("Patio")
        patio.join(kitchen)
        session = Session(household=house)
        self.addCleanup(session.close)
        session.press("kb:NVDA+shift+control+s")
        session.select_room("Kitchen")
        patio.online = False
        session.press("kb:rightArrow")
        self.assertEqual(session.press("kb:leftArrow")[-1],
                         "Kitchen, 25 percent; Patio, not answering")
        self.assertRegex(session.press("kb:upArrow")[-1], r"^\d+ percent$")
