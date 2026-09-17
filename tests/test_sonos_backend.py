"""Behavior checks without network traffic or NVDA imports."""
import unittest
from types import SimpleNamespace

from support import load

backend = load("backend")


class Player:
    def __init__(self, uid, name, visible=True):
        self.uid, self.player_name, self.is_visible = uid, name, visible
        self.volume, self.mute = 25, False
        self.state = "PLAYING"
        self.calls = []
        self.actions = "Play,Pause,Stop,Next,Previous"
        self.avTransport = SimpleNamespace(GetCurrentTransportActions=lambda args: {"Actions": self.actions})

    def get_current_transport_info(self):
        return {"current_transport_state": self.state}

    def get_current_track_info(self):
        return {"title": "Song", "artist": "Artist"}

    def pause(self):
        self.calls.append("pause")
        self.state = "PAUSED_PLAYBACK"

    def play(self):
        self.calls.append("play")
        self.state = "PLAYING"

    def next(self):
        self.calls.append("next")

    def stop(self):
        self.calls.append("stop")
        self.state = "STOPPED"

    def previous(self):
        self.calls.append("previous")


class Group:
    def __init__(self, *members):
        self.coordinator = members[0]
        self.members = members
        self.volume, self.mute = 25, False
        self.adjustments = []
        for member in members:
            member.group = self

    def set_relative_volume(self, delta):
        self.adjustments.append(delta)
        self.volume = max(0, min(100, self.volume + delta))
        return self.volume


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.era = Player("era", "Living room")
        self.satellite = Player("sat", "Living room right", False)
        self.move = Player("move", "Move")
        self.stereo = Group(self.era, self.satellite)
        self.portable = Group(self.move)
        self.saved = []
        self.controller = backend.Controller(lambda **kw: [self.move, self.era], remember=self.saved.append)

    def test_stereo_hidden_and_navigation_wraps(self):
        self.assertEqual(self.controller.execute("open"), "Sonos, Living room, 25 percent")
        self.assertEqual(self.controller.execute("left"), "Move, 25 percent")
        self.assertEqual(self.controller.execute("right"), "Living room, 25 percent")

    def test_group_dedup_and_coordinator(self):
        combined = Group(self.era, self.satellite, self.move)
        self.assertEqual(len(self.controller.groups()), 1)
        self.controller.execute("next")
        self.assertEqual(self.era.calls, ["next"])
        self.assertEqual(self.move.calls, [])
        self.assertNotIn("right", self.controller.describe(combined))

    def test_one_percent_and_limits(self):
        self.assertEqual(self.controller.execute("up"), "26 percent")
        self.assertEqual(self.controller.execute("down"), "25 percent")
        self.assertEqual(self.stereo.adjustments, [1, -1])
        self.stereo.volume = 100
        self.assertEqual(self.controller.execute("up"), "100 percent")
        self.stereo.volume = 0
        self.assertEqual(self.controller.execute("down"), "0 percent")

    def test_play_pause_mute_and_track(self):
        self.assertEqual(self.controller.execute("playPause"), "Pause")
        self.assertEqual(self.controller.execute("playPause"), "Play")
        self.assertEqual(self.controller.execute("mute"), "Muted")
        self.assertEqual(self.controller.execute("mute"), "Unmuted")
        self.assertEqual(self.controller.execute("playing"), "Song, Artist, playing")
        self.controller.execute("previous")
        self.assertIn("previous", self.era.calls)

    def test_remembered_room_survives_grouping(self):
        self.controller.selected = "move"
        self.assertIs(self.controller.current(), self.portable)
        combined = Group(self.era, self.move, self.satellite)
        self.assertIs(self.controller.current(), combined)

    def test_no_speakers(self):
        controller = backend.Controller(lambda **kw: None)
        with self.assertRaisesRegex(RuntimeError, "No Sonos rooms"):
            controller.execute("open")

    def test_radio_uses_stop_and_rejects_skipping(self):
        self.era.actions = "Set, Stop, Play"
        self.assertEqual(self.controller.execute("playPause"), "Stopped")
        self.assertEqual(self.era.calls, ["stop"])
        self.assertEqual(self.controller.execute("playPause"), "Play")
        self.assertIn("not available", self.controller.execute("next"))
        self.assertNotIn("next", self.era.calls)

    def test_radio_metadata_fallback(self):
        self.era.get_current_track_info = lambda: {"metadata": '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/"><item><dc:title>njoy.mp3</dc:title></item></DIDL-Lite>'}
        self.assertEqual(self.controller.execute("playing"), "njoy.mp3, playing")

    def test_battery(self):
        self.move.get_battery_info = lambda timeout: {"Level": 98, "PowerSource": "BATTERY"}
        self.controller.selected = "move"
        self.assertEqual(self.controller.execute("battery"), "Move, battery 98 percent, power source battery")

    def test_nonportable_battery(self):
        class NotSupportedException(Exception):
            pass
        def unsupported(timeout):
            raise NotSupportedException()
        self.era.get_battery_info = unsupported
        self.assertIn("not available", self.controller.execute("battery"))


if __name__ == "__main__":
    unittest.main()
