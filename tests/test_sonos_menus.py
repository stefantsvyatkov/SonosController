"""Menu selection, timer staging, playback routes, and alarm persistence."""
from datetime import time
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_sonos_backend import Player, Group, backend
from support import load

menus = load("menus")
Favorites, SleepTimer, AlarmMenu = menus.Favorites, menus.SleepTimer, menus.AlarmMenu
valid_url, parse_alarm = menus.valid_url, menus.parse_alarm
vendors = load("vendors")


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.player = Player("one", "Room")
        Group(self.player)
        self.controller = backend.Controller(lambda **kw: [self.player])
        self.calls = []

    def test_url_clipboard_filter(self):
        for value in ("https://example.com/song.mp3", " http://192.168.0.5:8000/radio "):
            self.assertTrue(valid_url(value))
        for value in ("hello", "a link https://example.com", "javascript:alert(1)", "file:///a.mp3", "https://", "http://a/b\ntext"):
            self.assertFalse(valid_url(value))

    def favorite(self, uri, kind):
        return SimpleNamespace(title="Favorite", resource_meta_data="<metadata/>",
                               reference=SimpleNamespace(resources=[SimpleNamespace(uri=uri)], item_class=kind))

    def test_radio_favorite_direct_and_song_enqueued(self):
        items = [self.favorite("x-sonosapi-stream:station", "object.item.audioItem.audioBroadcast"),
                 self.favorite("x-sonos-spotify:track", "object.item.audioItem.musicTrack")]
        self.player.music_library = SimpleNamespace(get_sonos_favorites=lambda **kw: items)
        self.player.play_uri = lambda uri, meta, start=False: self.calls.append(("uri", uri, meta))
        self.player.add_to_queue = lambda ref: 7
        self.player.play_from_queue = lambda index, start=False: self.calls.append(("queue", index))
        menu = Favorites(self.controller)
        self.assertEqual(self.calls, [])
        with patch("sonosAddon.menus.selected_uri", return_value=True):
            menu.play()
            menu.move(1)
            menu.play()
        self.assertEqual(self.calls[-1], ("queue", 6))
        self.assertEqual(self.calls[0][0], "uri")

    def test_catalog_containers_are_excluded(self):
        empty = SimpleNamespace(title="Discover Sonos Radio", reference=SimpleNamespace(resources=[], item_class="object.container"))
        song = self.favorite("x-sonos-spotify:track", "object.item.audioItem.musicTrack")
        self.player.music_library = SimpleNamespace(get_sonos_favorites=lambda **kw: [empty, song])
        self.assertEqual(Favorites(self.controller).items, [song])

    def test_timer_is_staged_and_custom_is_one_minute(self):
        self.player.get_sleep_timer = lambda: None
        self.player.set_sleep_timer = self.calls.append
        menu = SleepTimer(self.controller)
        self.assertEqual(menu.describe(), "Timer off")
        menu.adjust(1)
        self.assertEqual(menu.describe(), "5 minutes")
        self.assertEqual(self.calls, [])
        menu.apply()
        self.assertEqual(self.calls, [300])
        menu.move(1)
        self.assertEqual(menu.describe(), "Custom, 5 minutes.")
        self.assertEqual(menu.adjust(1), "6 minutes")
        menu.apply()
        self.assertEqual(self.calls[-1], 360)
        menu.move(-1)
        menu.index = 0
        menu.apply()
        self.assertIsNone(self.calls[-1])

    def test_timer_values_are_bounded_to_ninety_minutes(self):
        self.player.get_sleep_timer = lambda: None
        menu = SleepTimer(self.controller)
        for _ in range(30):
            menu.adjust(1)
        self.assertEqual(menu.describe(), "90 minutes")
        menu.move(1)
        for _ in range(100):
            menu.adjust(1)
        self.assertEqual(menu.describe(), "Custom, 90 minutes.")

    def test_elapsed_remaining_and_copy_guard(self):
        self.player.get_current_track_info = lambda: {"title": "Song", "artist": "Artist", "position": "0:01:00", "duration": "0:03:00"}
        self.assertEqual(self.controller.execute("elapsed"), "Elapsed 0:01:00")
        self.assertEqual(self.controller.execute("remaining"), "Remaining 0:02:00")
        self.assertEqual(self.controller.execute("copyTrack"), {"copy": "Song — Artist"})
        self.player.get_current_track_info = lambda: {"title": "TV", "uri": "x-sonos-htastream:tv"}
        self.assertIn("not available", self.controller.execute("copyTrack"))
        self.assertIn("unavailable", self.controller.execute("remaining"))


class FakeAlarm:
    def __init__(self, zone):
        self.zone = zone
        self.room_uuid = zone.uid
        self.start_time = time(7)
        self.duration = None
        self.enabled = True
        self.recurrence = "DAILY"
        self.program_uri = "original"
        self.program_metadata = "metadata"
        self.play_mode = "NORMAL"
        self.volume = 20
        self.include_linked_zones = False
        self.alarm_id = "1"
        self.saves = []

    def update(self, **values):
        self.__dict__.update(values)

    def save(self):
        self.saves.append(self)
        return self.alarm_id

    def remove(self):
        self.removed = True


class AlarmTests(unittest.TestCase):
    def setUp(self):
        self.room = Player("one", "Room")
        self.room.music_library = SimpleNamespace(get_sonos_favorites=lambda **kw: [])
        Group(self.room)
        self.alarm = FakeAlarm(self.room)
        class Store:
            def update(store, player): pass
            def update_skipped(store, player): pass
            def __iter__(store): return iter([self.alarm])
        alarm_module = SimpleNamespace(
            Alarms=Store,
            Alarm=FakeAlarm,
            is_valid_recurrence=lambda text: text in ("DAILY", "ONCE", "WEEKDAYS", "WEEKENDS", "ON_135"),
        )
        self.patcher = patch.object(vendors, "soco_submodule", return_value=alarm_module)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.controller = backend.Controller(lambda **kw: [self.room])
        self.menu = AlarmMenu(self.controller)

    def data(self):
        return dict(time="08:35", duration="01:00", recurrence="ON_135", enabled=False,
                    volume=30, linked=True, play_mode="REPEAT_ALL", room=0, source=0, url="")

    def test_save_preserves_existing_source_and_edits_all_fields(self):
        context = self.menu.editor()
        self.menu.save((self.data(), context))
        saved = self.alarm.saves[-1]
        self.assertEqual(saved.start_time, time(8, 35))
        self.assertEqual(saved.duration, time(1))
        self.assertEqual(saved.program_uri, "original")
        self.assertEqual(saved.program_metadata, "metadata")
        self.assertFalse(saved.enabled)
        self.assertTrue(saved.include_linked_zones)
        self.assertEqual(saved.volume, 30)
        self.assertEqual(saved.recurrence, "ON_135")
        self.assertEqual(self.alarm.start_time, time(7))

    def test_bad_time_recurrence_and_url_do_not_save(self):
        for key, value in (("time", "25:70"), ("duration", "nonsense"), ("recurrence", "bad")):
            data = self.data()
            data[key] = value
            with self.assertRaises(ValueError):
                parse_alarm(data)
        data = self.data()
        data.update(source=2, url="not a url")
        with self.assertRaises(ValueError):
            self.menu.save((data, self.menu.editor()))
        self.assertEqual(self.alarm.saves, [])

    def test_alarm_favorite_is_saved_with_metadata(self):
        favorite = SimpleNamespace(title="Radio", resource_meta_data="radio metadata",
            reference=SimpleNamespace(resources=[SimpleNamespace(uri="x-sonosapi-stream:station")],
                                      item_class="object.item.audioItem.audioBroadcast"))
        self.room.music_library = SimpleNamespace(get_sonos_favorites=lambda **kw: [favorite])
        context = self.menu.editor()
        data = self.data()
        data["source"] = 3
        self.menu.save((data, context))
        self.assertEqual(self.alarm.saves[-1].program_uri, "x-sonosapi-stream:station")
        self.assertEqual(self.alarm.saves[-1].program_metadata, "radio metadata")

    def test_toggle_failure_restores_state(self):
        def fail(): raise OSError("unreachable")
        self.alarm.save = fail
        with self.assertRaises(OSError):
            self.menu.toggle()
        self.assertTrue(self.alarm.enabled)

    def test_delete_uses_id_and_new_entry(self):
        self.menu.move(1)
        self.assertIn("New alarm", self.menu.describe())
        self.menu.delete("1")
        self.assertTrue(self.alarm.removed)
