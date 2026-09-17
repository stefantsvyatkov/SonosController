"""Recover ambiguous Play responses without accepting the wrong source."""
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from test_sonos_backend import backend
from support import load

playback = load("playback")
select_and_play, source_identity = playback.select_and_play, playback.source_identity

class PlaybackConfirmationTests(unittest.TestCase):
    def test_play_timeout_after_success_is_not_reported_as_failure(self):
        player = SimpleNamespace(play=Mock(side_effect=TimeoutError()), get_current_transport_info=lambda: {"current_transport_state": "PLAYING"})
        select = Mock()
        self.assertTrue(select_and_play(player, select, lambda: True))
        select.assert_called_once()
        player.play.assert_called_once()

    def test_other_playing_source_is_not_success(self):
        player = SimpleNamespace(play=Mock(side_effect=TimeoutError()), get_current_transport_info=lambda: {"current_transport_state": "PLAYING"})
        with self.assertRaisesRegex(RuntimeError, "source changed"):
            select_and_play(player, Mock(), lambda: False)

    def test_transient_701_retries_play_only(self):
        class NotReady(Exception): error_code = "701"
        player = SimpleNamespace(play=Mock(side_effect=[NotReady(), None]),
            get_current_transport_info=Mock(side_effect=[{"current_transport_state": "STOPPED"}, {"current_transport_state": "PLAYING"}]))
        select = Mock()
        with patch("sonosAddon.playback.time.sleep"):
            self.assertTrue(select_and_play(player, select, lambda: True))
        self.assertEqual(player.play.call_count, 2)
        select.assert_called_once()

    def test_selection_timeout_does_not_play_wrong_source(self):
        player = SimpleNamespace(play=Mock())
        with self.assertRaisesRegex(RuntimeError, "selection could not"):
            select_and_play(player, Mock(side_effect=TimeoutError()), lambda: False)
        player.play.assert_not_called()

    def test_cancel_after_selection_does_not_play(self):
        cancelled = [False]
        player = SimpleNamespace(play=Mock())
        def select(): cancelled[0] = True
        self.assertFalse(select_and_play(player, select, lambda: True, lambda: cancelled[0]))
        player.play.assert_not_called()

    def test_sonos_flags_change_but_station_and_account_still_match(self):
        a = "x-sonosapi-stream:r%3A402661?sid=268&flags=8292&sn=3"
        b = "x-sonosapi-stream:r%3a402661?sid=268&flags=8232&sn=3"
        self.assertEqual(source_identity(a), source_identity(b))
        self.assertNotEqual(source_identity(a), source_identity(b.replace("402661", "999")))
        self.assertNotEqual(source_identity(a), source_identity(b.replace("sn=3", "sn=4")))

    def test_buffering_is_starting_not_failure(self):
        player = SimpleNamespace(play=Mock(), get_current_transport_info=lambda: {"current_transport_state": "TRANSITIONING"})
        with patch("sonosAddon.playback.time.sleep"):
            self.assertEqual(select_and_play(player, Mock(), lambda: True), "starting")
        player.play.assert_called_once()
