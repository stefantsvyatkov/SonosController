"""Local favorites and the history (F, Shift+F, H, Delete and F2), through the emulator.

The owner's polls of 10 and 11 November 2026 set the behaviour: F has two
sections, Sonos favorites and Local favorites, switched with Left and Right;
Shift+F adds what the add-on is playing to Local favorites and what the Sonos app
started to Sonos favorites, and in the history the selected entry; Delete asks
before removing anything; F2 renames, in both sections.
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
from harness.sonos import DidlItem, DidlMusicTrack, DidlResource, didl
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

    def test_shift_f_on_a_sonos_favorite_says_it_already_is_one(self):
        self.press("kb:f")
        self.press("kb:enter")
        self.assertEqual(self.press("kb:shift+f"), "Test Radio is already a Sonos favorite.")

    def test_a_folder_played_with_control_f_can_be_added(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        localfiles = sys.modules[self.session.package.__name__ + ".localfiles"]

        def play(player, selection, cancelled=lambda: False):
            # The speaker plays the add-on's files from this computer, through its queue.
            player = player.group.coordinator
            address = localfiles.local_address(player)
            track = DidlMusicTrack("one", resources=[DidlResource(f"http://{address}:8000/a/one.mp3")])
            player.clear_queue()
            player.add_multiple_to_queue([track])
            player.play_from_queue(0)
            return "Playing one"

        self.session.plugin.localFiles.play = play
        self.session.plugin._enqueue(("localPlay", (self.session.household.players[0],
                                                    {"path": folder, "recursive": True})))
        self.session.settle()
        self.session.select_room("Living Room")
        heard = self.press("kb:shift+f")
        self.assertEqual(heard, f"Added {Path(folder).name} to Local favorites.")
        self.assertEqual(self.stored()["favorites"][0]["recursive"], True)


class AddingFromSonos(LibraryTest):
    """Shift+F on what the Sonos app started: Sonos favorites (the owner's poll, 11 November 2026)."""

    def setUp(self):
        super().setUp()
        self.living = self.session.household.find("Living Room")

    def sonos_titles(self):
        return [f.title for f in self.session.household.favorites]

    def test_a_station_goes_to_sonos_favorites(self):
        self.living.play_uri("x-sonosapi-stream:s1234?sid=254&flags=8232&sn=1", meta=didl("Radio Paradise"))
        self.assertEqual(self.press("kb:shift+f"), "Added Radio Paradise to Sonos favorites.")
        self.assertEqual(self.press("kb:shift+f"), "Radio Paradise is already a Sonos favorite.")
        self.assertEqual(self.sonos_titles(), ["Test Radio", "Jazz Playlist", "Radio Paradise"])
        self.assertFalse((self.session.config_dir / library.FILE_NAME).exists(), "nothing goes to Local favorites")
        self.assertEqual(self.press("kb:f"), "Sonos favorites. Test Radio, 1 of 3")

    def test_a_favorite_the_sonos_app_started_is_one_already(self):
        # The speaker reports other flags than the favorite keeps.
        self.living.play_uri("x-sonosapi-stream:test?sid=254&flags=8232", meta=didl("Test Radio"))
        self.session.household.reset_calls()
        self.assertEqual(self.press("kb:shift+f"), "Test Radio is already a Sonos favorite.")
        self.assertNotIn("CreateObject", self.session.household.calls)

    def test_an_album_from_the_queue_is_added_whole(self):
        album = DidlItem("Blue Lines", "object.container.album.musicAlbum", "album1",
                         [DidlResource("x-rincon-cpcontainer:album1?sid=12&flags=8300&sn=1")])
        self.living.clear_queue()
        self.living.add_to_queue(album)
        self.living.play_from_queue(0)
        self.assertEqual(self.press("kb:shift+f"), "Added Blue Lines to Sonos favorites.")
        added = self.session.household.favorites[-1]
        self.assertEqual(added.reference.resources[0].uri, "x-rincon-cpcontainer:album1?sid=12&flags=8300&sn=1")
        self.assertEqual(added.reference.item_class, "object.container.album.musicAlbum")
        self.assertTrue(all(s.unsubscribed for s in self.session.household.subscriptions))

    def test_a_queue_of_single_songs_has_no_album_to_add(self):
        self.living.clear_queue()
        self.living.add_multiple_to_queue([DidlMusicTrack("one", resources=[DidlResource("x-file-cifs://nas/one.mp3")])])
        self.living.play_from_queue(0)
        self.assertEqual(self.press("kb:shift+f"), "Could not find out which album or playlist is playing.")
        self.assertEqual(len(self.session.household.favorites), 2)

    def test_events_kept_out_by_a_firewall_are_named(self):
        menus = sys.modules[self.session.package.__name__ + ".menus"]
        self.addCleanup(setattr, menus, "EVENT_WAIT", menus.EVENT_WAIT)
        menus.EVENT_WAIT = 0.1
        self.living.events_blocked = True
        self.living.add_to_queue(DidlItem("Blue Lines", "object.container.album.musicAlbum", "album1",
                                          [DidlResource("x-rincon-cpcontainer:album1?sid=12")]))
        self.living.play_from_queue(0)
        self.assertEqual(self.press("kb:shift+f"),
                         "The speaker did not say what is playing. Check firewall access for NVDA.")
        self.assertTrue(all(s.unsubscribed for s in self.session.household.subscriptions))

    def test_tv_and_an_empty_room_cannot_be_added(self):
        self.living.play_uri(f"x-sonos-htastream:{self.living.uid}:spdif")
        self.assertEqual(self.press("kb:shift+f"), "This source cannot be added to favorites.")
        self.session.select_room("Kitchen")
        self.assertEqual(self.press("kb:shift+f"), "Nothing is selected in this room.")
        self.assertEqual(len(self.session.household.favorites), 2)

    def test_a_stream_this_computer_serves_never_becomes_a_sonos_favorite(self):
        localfiles = sys.modules[self.session.package.__name__ + ".localfiles"]
        address = localfiles.local_address(self.living)
        self.living.play_uri(f"x-rincon-mp3radio://{address}:9000/relay", meta=didl("Relayed"))
        self.assertEqual(self.press("kb:shift+f"), "This source cannot be added to favorites.")
        self.assertEqual(len(self.session.household.favorites), 2)

    def test_a_full_list_is_said(self):
        self.session.household.favorites_limit = 2
        self.living.play_uri("x-sonosapi-stream:s1234?sid=254", meta=didl("Radio Paradise"))
        self.assertEqual(self.press("kb:shift+f"), "Sonos favorites are full. Remove some first.")


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


class SonosFavorites(LibraryTest):
    """F2 and Delete in the Sonos section (the owner's poll of 10 November 2026)."""

    def titles(self):
        return [f.title for f in self.session.household.favorites]

    def test_f2_renames_a_sonos_favorite_for_the_whole_home(self):
        self.press("kb:f")
        self.press("kb:downArrow")
        self.session.responder.text_answer = "Джаз  вечер"
        self.assertEqual(self.press("kb:f2"), "Renamed. Джаз вечер, 2 of 2")
        self.assertEqual(self.session.responder.text_entries[-1].field.GetValue(), "Джаз  вечер")
        self.assertEqual(self.titles(), ["Test Radio", "Джаз вечер"])
        self.assertEqual(self.session.mode, "menu:favorites")

    def test_delete_asks_and_removes_a_sonos_favorite(self):
        self.press("kb:f")
        self.session.responder.message_answer = self.session.wx.NO
        self.assertEqual(self.session.press("kb:delete"), [])
        self.assertEqual(self.session.responder.message_boxes[-1][1], "Remove Test Radio from Sonos favorites?")
        self.assertEqual(self.titles(), ["Test Radio", "Jazz Playlist"])
        self.session.responder.message_answer = self.session.wx.YES
        self.assertEqual(self.press("kb:delete"), "Removed. Jazz Playlist, 1 of 1")
        self.assertEqual(self.titles(), ["Jazz Playlist"])
        self.assertEqual(self.press("kb:delete"), "Removed. No Sonos favorites. Add them in the Sonos app.")

    def test_a_favorite_removed_in_the_sonos_app_meanwhile_is_said(self):
        self.press("kb:f")
        self.session.household.favorites.pop(0)
        self.session.responder.message_answer = self.session.wx.YES
        self.assertEqual(self.press("kb:delete"), "This item is no longer in the list.")


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

    def test_a_pasted_page_is_cut_to_a_name(self):
        self.play_station()
        self.press("kb:h")
        self.session.responder.text_answer = "x" * 2000
        self.press("kb:f2")
        self.assertEqual(len(self.stored()["history"][0]["title"]), library.TITLE_SIZE)

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
