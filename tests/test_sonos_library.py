"""Local favorites and the history (F, Shift+F, H, Delete and F2), through the emulator.

The owner's poll of 10 November 2026 set the behaviour: F has two sections,
Sonos favorites and Local favorites, switched with Left and Right; Shift+F adds
what the add-on is playing, and in the history the selected entry; Delete asks
before removing anything; F2 renames; Sonos favorites are not changed from here.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import Session
from support import load

library = load("library")


class Relay:
    """Plays a URL on the simulated speaker the way the real relay reports it."""

    def __init__(self, played, names):
        self.played = played
        self.names = names
        self.calls = []

    def play_url(self, player, url, cancelled, progress, addresses=()):
        self.calls.append(url)
        player.play_uri(url, meta="", start=True)
        name = self.names.get(url, url)
        return self.played(f"Playing {name}", name)

    def stop(self):
        pass

    def close(self):
        pass

    def recheck(self):
        pass


STATION = "http://radio.example/live.mp3"


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.session = Session()
        self.addCleanup(self.session.close)
        util = sys.modules[self.session.package.__name__ + ".util"]
        self.relay = Relay(util.Played, {STATION: "Example Radio"})
        self.session.plugin.relay = self.relay
        self.session.press("kb:NVDA+shift+control+s")
        self.session.select_room("Living Room")
        self.session.speech.drain()

    def press(self, *keys):
        return self.session.press(*keys)[-1]

    def play_station(self):
        self.session.responder.text_answer = STATION
        return self.press("kb:u")

    def stored(self):
        return json.loads((self.session.config_dir / library.FILE_NAME).read_text(encoding="utf-8"))


class Adding(LibraryTest):
    def test_shift_f_adds_the_station_the_add_on_is_playing(self):
        self.assertEqual(self.play_station(), "Playing Example Radio")
        self.assertEqual(self.press("kb:shift+f"), "Added Example Radio to Local favorites.")
        self.assertEqual(self.press("kb:shift+f"), "Example Radio is already in Local favorites.")
        self.assertEqual([e["url"] for e in self.stored()["favorites"]], [STATION])

    def test_shift_f_refuses_what_the_add_on_did_not_start(self):
        heard = self.press("kb:shift+f")
        self.assertTrue(heard.startswith("What is playing here was not started from the add-on."), heard)
        self.assertFalse((self.session.config_dir / library.FILE_NAME).exists(), "nothing should be saved")

    def test_shift_f_on_a_sonos_favorite_says_it_already_is_one(self):
        self.press("kb:f")
        self.press("kb:enter")
        self.assertEqual(self.press("kb:shift+f"), "Test Radio is already a Sonos favorite.")

    def test_a_folder_played_with_control_f_can_be_added(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        played = []

        def play(player, selection, cancelled=lambda: False):
            played.append(selection)
            player.group.coordinator.play_uri("x-rincon-queue:living#0", meta="", start=True)
            return "Playing 3 files."

        self.session.plugin.localFiles.play = play
        self.session.plugin._enqueue(("localPlay", (self.session.household.players[0],
                                                    {"path": folder, "recursive": True})))
        self.session.settle()
        self.session.select_room("Living Room")
        heard = self.press("kb:shift+f")
        self.assertEqual(heard, f"Added {Path(folder).name} to Local favorites.")
        self.assertEqual(self.stored()["favorites"][0]["recursive"], True)


class Sections(LibraryTest):
    def add_station(self):
        self.play_station()
        self.press("kb:shift+f")

    def test_left_and_right_switch_sections_and_keep_their_places(self):
        self.add_station()
        self.assertEqual(self.press("kb:f"), "Sonos favorites. Test Radio, 1 of 2")
        self.assertEqual(self.press("kb:downArrow"), "Jazz Playlist, 2 of 2")
        self.assertEqual(self.press("kb:rightArrow"), "Local favorites. Example Radio, 1 of 1")
        self.assertEqual(self.press("kb:leftArrow"), "Sonos favorites. Jazz Playlist, 2 of 2")

    def test_an_empty_local_section_says_how_to_fill_it(self):
        self.press("kb:f")
        self.assertEqual(self.press("kb:rightArrow"), "No local favorites. Shift+F adds what is playing.")
        self.assertEqual(self.press("kb:enter"), "No local favorites. Shift+F adds what is playing.")

    def test_enter_plays_a_local_favorite_and_leaves_the_menu(self):
        self.add_station()
        self.relay.calls.clear()
        self.press("kb:f")
        self.press("kb:rightArrow")
        self.assertEqual(self.press("kb:enter"), "Playing Example Radio")
        self.assertEqual(self.relay.calls, [STATION])
        self.assertEqual(self.session.mode, "layer")

    def test_sonos_favorites_are_not_changed_from_here(self):
        self.press("kb:f")
        for key in ("kb:delete", "kb:f2"):
            self.assertEqual(self.press(key), "Sonos favorites are changed in the Sonos app.")
        self.assertEqual(self.session.responder.message_boxes, [])
        self.assertEqual(self.session.responder.text_entries, [])


class Removing(LibraryTest):
    def setUp(self):
        super().setUp()
        self.play_station()
        self.press("kb:shift+f")
        self.press("kb:f")
        self.press("kb:rightArrow")

    def test_delete_asks_and_no_keeps_the_favorite(self):
        self.session.responder.message_answer = self.session.wx.NO
        # Like an alarm's deletion, saying No closes the question and nothing more.
        self.assertEqual(self.session.press("kb:delete"), [])
        caption, question, style = self.session.responder.message_boxes[-1]
        self.assertEqual(question, "Remove Example Radio from Local favorites?")
        self.assertTrue(style & self.session.wx.NO_DEFAULT, "No must be the default answer")
        self.assertEqual(len(self.stored()["favorites"]), 1)
        self.assertEqual(self.session.mode, "menu:favorites")

    def test_delete_and_yes_removes_it(self):
        self.session.responder.message_answer = self.session.wx.YES
        self.assertEqual(self.press("kb:delete"),
                         "Removed. No local favorites. Shift+F adds what is playing.")
        self.assertEqual(self.stored()["favorites"], [])


class Renaming(LibraryTest):
    def test_f2_renames_a_local_favorite(self):
        self.play_station()
        self.press("kb:shift+f")
        self.press("kb:f")
        self.press("kb:rightArrow")
        self.session.responder.text_answer = "  My   station "
        self.assertEqual(self.press("kb:f2"), "Renamed. My station, 1 of 1")
        dialog = self.session.responder.text_entries[-1]
        self.assertEqual(dialog.title, "Rename")
        self.assertEqual(self.stored()["favorites"][0]["title"], "My station")

    def test_an_unchanged_name_is_said(self):
        self.play_station()
        self.press("kb:h")
        self.session.responder.text_answer = "Example Radio"
        self.assertEqual(self.press("kb:f2"), "Name not changed.")

    def test_a_renamed_history_entry_keeps_its_name_when_played_again(self):
        self.play_station()
        self.press("kb:h")
        self.session.responder.text_answer = "Morning radio"
        self.press("kb:f2")
        self.press("kb:escape")
        self.play_station()
        self.assertEqual(self.press("kb:h"), "History. Morning radio, 1 of 1")


class HistoryList(LibraryTest):
    def test_empty_history_is_said_and_the_menu_closes(self):
        self.assertEqual(self.press("kb:h"), "History is empty.")
        self.assertEqual(self.session.mode, "layer")

    def test_newest_first_and_a_replay_moves_to_the_top(self):
        self.play_station()
        self.press("kb:f")
        self.press("kb:enter")
        self.assertEqual(self.press("kb:h"), "History. Test Radio, 1 of 2")
        self.assertEqual(self.press("kb:downArrow"), "Example Radio, 2 of 2")
        self.assertEqual(self.press("kb:enter"), "Playing Example Radio")
        self.assertEqual(self.press("kb:h"), "History. Example Radio, 1 of 2")

    def test_shift_f_in_the_history_adds_the_selected_entry(self):
        self.play_station()
        self.press("kb:h")
        self.assertEqual(self.press("kb:shift+f"), "Added Example Radio to Local favorites.")
        self.assertEqual(self.session.mode, "menu:history")

    def test_delete_asks_before_removing_from_the_history(self):
        self.play_station()
        self.press("kb:h")
        self.session.responder.message_answer = self.session.wx.YES
        self.assertEqual(self.press("kb:delete"), "Removed. History is empty.")
        self.assertEqual(self.session.responder.message_boxes[-1][1],
                         "Remove Example Radio from the history?")
        self.assertEqual(self.stored()["history"], [])

    def test_a_sonos_favorite_gone_from_the_household_is_reported(self):
        self.press("kb:f")
        self.press("kb:enter")
        self.session.household.favorites.clear()
        self.press("kb:h")
        self.assertEqual(self.press("kb:enter"), "This Sonos favorite is no longer available.")

    def test_the_history_keeps_fifty(self):
        store = library.Library(self.session.config_dir / "other.json")
        for number in range(60):
            store.remember(library.url_entry(f"http://radio.example/{number}"))
        self.assertEqual(len(store.history), library.HISTORY_SIZE)
        self.assertEqual(store.history[0]["url"], "http://radio.example/59")


class Storage(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.path = self.folder / library.FILE_NAME

    def test_entries_survive_a_restart(self):
        first = library.Library(self.path)
        first.add_favorite(library.url_entry(STATION, "Example Radio"))
        first.remember(library.url_entry(STATION, "Example Radio"), media=STATION)
        second = library.Library(self.path)
        second.load()
        self.assertEqual(second.favorites[0]["title"], "Example Radio")
        self.assertNotIn("media", second.favorites[0])
        self.assertEqual(second.playing(STATION)["url"], STATION)

    def test_a_damaged_file_is_set_aside_not_overwritten(self):
        self.path.write_text("{not json", encoding="utf-8")
        store = library.Library(self.path)
        store.add_favorite(library.url_entry(STATION))
        self.assertEqual(self.path.with_suffix(".json.bad").read_text(encoding="utf-8"), "{not json")
        self.assertEqual(len(store.favorites), 1)

    def test_invalid_entries_are_skipped(self):
        self.path.write_text(json.dumps({"favorites": [
            {"kind": "url", "title": "Good", "url": STATION},
            {"kind": "url", "title": "Bad", "url": "javascript:alert(1)"},
            {"kind": "files", "title": "", "path": "C:/Music"},
            {"kind": "other", "title": "Odd"},
        ]}), encoding="utf-8")
        store = library.Library(self.path)
        store.load()
        self.assertEqual([e["title"] for e in store.favorites], ["Good"])


if __name__ == "__main__":
    unittest.main()
