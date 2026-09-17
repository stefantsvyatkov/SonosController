"""Experimental URL fallback sequencing without network access."""

from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import load

relay_urls = load("relay_urls")
urls = load("urls")


class Player:
    def __init__(self):
        self.group = types.SimpleNamespace(coordinator=self)


class RelayUrlTests(unittest.TestCase):
    def test_direct_success_never_starts_relay(self):
        source = urls.AudioSource("http://example.com/live.mp3", "radio", "audio/mpeg", "Radio")
        relay = types.SimpleNamespace(
            available=True,
            stop=lambda *args: None,
            start=lambda *args: self.fail("relay started"),
        )
        with patch.object(relay_urls, "resolve_audio", return_value=source), \
                patch.object(relay_urls, "_direct", return_value=True):
            self.assertEqual(relay_urls.play_url(Player(), source.url, relay=relay), "Playing Radio")

    def test_direct_then_remux_then_transcode(self):
        source = urls.AudioSource("http://example.com/live.mp3", "radio", "audio/mpeg", "Radio")
        sessions = []

        class Session:
            def __init__(self, mode):
                self.url = "http://127.0.0.1/" + mode
                self.content_type = "audio/mpeg"
                self.requested = threading.Event()
                self.requested.set()
                self.failed = threading.Event()

        class Relay:
            available = True

            def stop(self, *args):
                pass

            def start(self, player, resolved, mode, clients):
                del player, resolved, clients
                sessions.append(Session(mode))
                return sessions[-1]

        with patch.object(relay_urls, "resolve_audio", return_value=source), \
                patch.object(relay_urls, "_direct", side_effect=RuntimeError("The speaker did not start the stream.")), \
                patch.object(relay_urls, "_confirmed_start", side_effect=[RuntimeError("Local audio fallback failed."), True]):
            self.assertEqual(relay_urls.play_url(Player(), source.url, relay=Relay()), "Playing Radio")
        self.assertEqual(
            [session.url.rsplit("/", 1)[1] for session in sessions],
            ["remux_mp3", "transcode_mp3"],
        )

    def test_unreachable_endpoint_does_not_start_second_encoder(self):
        source = urls.AudioSource("http://example.com/live.mp3", "radio", "audio/mpeg", "Radio")
        modes = []

        class Relay:
            available = True

            def stop(self, *args):
                pass

            def start(self, player, resolved, mode, clients):
                del player, resolved, clients
                modes.append(mode)
                return types.SimpleNamespace(
                    url="http://127.0.0.1/missing",
                    content_type="audio/mpeg",
                    requested=threading.Event(),
                    failed=threading.Event(),
                )

        with patch.object(relay_urls, "resolve_audio", return_value=source), \
                patch.object(relay_urls, "_direct", side_effect=RuntimeError("The speaker did not start the stream.")), \
                patch.object(relay_urls, "_confirmed_start", side_effect=RuntimeError("The speaker did not start the stream.")):
            with self.assertRaisesRegex(RuntimeError, "firewall"):
                relay_urls.play_url(Player(), source.url, relay=Relay())
        self.assertEqual(modes, ["remux_mp3"])

    def test_source_change_does_not_use_fallback(self):
        source = urls.AudioSource("http://example.com/live.mp3", "radio", "audio/mpeg", "Radio")
        relay = types.SimpleNamespace(
            available=True,
            stop=lambda *args: None,
            start=lambda *args: self.fail("relay started"),
        )
        with patch.object(relay_urls, "resolve_audio", return_value=source), \
                patch.object(relay_urls, "_direct", side_effect=RuntimeError("Playback source changed.")):
            with self.assertRaisesRegex(RuntimeError, "source changed"):
                relay_urls.play_url(Player(), source.url, relay=relay)

    def test_only_decoder_related_errors_allow_fallback(self):
        for code in ("703", "704", "714", "716", "737", "738", "739"):
            error = RuntimeError("UPnP error")
            error.error_code = code
            self.assertTrue(relay_urls.fallback_allowed(error), code)
        self.assertTrue(relay_urls.fallback_allowed(RuntimeError("The speaker rejected the stream.")))
        error = RuntimeError("Transition unavailable")
        error.error_code = "701"
        self.assertFalse(relay_urls.fallback_allowed(error))


if __name__ == "__main__":
    unittest.main()
