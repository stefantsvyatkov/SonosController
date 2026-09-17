"""Local audio is scoped to selected files and supports Sonos range requests."""
import os
from pathlib import Path
import tempfile
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from types import SimpleNamespace
from test_sonos_backend import backend
from support import load

localfiles = load("localfiles")
AudioServer, audio_files = localfiles.AudioServer, localfiles.audio_files
PlaybackSettings = load("playbacksettings").PlaybackSettings


class LocalFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "track 2.mp3"
        self.file.write_bytes(b"0123456789")

    def test_folder_order_filter_and_optional_subfolders(self):
        (self.root / "track 10.mp3").write_bytes(b"audio")
        (self.root / "track 1.mp3").write_bytes(b"audio")
        (self.root / "private.txt").write_text("secret")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "other.flac").write_bytes(b"audio")
        self.assertEqual([Path(f).name for f in audio_files(self.root)], ["track 1.mp3", "track 2.mp3", "track 10.mp3"])
        self.assertEqual(len(audio_files(self.root, True)), 4)
        with self.assertRaisesRegex(RuntimeError, "No supported"):
            audio_files(self.root / "private.txt")

    def test_http_get_head_ranges_and_scope(self):
        server = AudioServer("127.0.0.1")
        self.addCleanup(server.close)
        url, mime = server.register(self.file)
        with urlopen(Request(url, method="HEAD"), timeout=3) as r:
            self.assertEqual(r.headers["Content-Length"], "10")
            self.assertEqual(r.read(), b"")
        self.assertFalse(server.was_requested(url))
        with urlopen(Request(url, headers={"Range": "bytes=2-5"}), timeout=3) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.headers["Content-Range"], "bytes 2-5/10")
            self.assertEqual(r.read(), b"2345")
        self.assertTrue(server.was_requested(url))
        with urlopen(Request(url, headers={"Range": "bytes=-3"}), timeout=3) as r:
            self.assertEqual(r.read(), b"789")
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(url, headers={"Range": "bytes=99-"}), timeout=3)
        self.assertEqual(error.exception.code, 416)
        error.exception.close()
        with self.assertRaises(HTTPError) as error:
            urlopen(url.rsplit("/", 1)[0] + "/../private.txt", timeout=3)
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        self.file.unlink()
        with self.assertRaises(HTTPError) as error:
            urlopen(url, timeout=3)
        self.assertEqual(error.exception.code, 404)
        error.exception.close()


class PlaybackTests(unittest.TestCase):
    def player(self):
        p = SimpleNamespace(shuffle=True, repeat=True, cross_fade=False,
            get_current_media_info=lambda: {"uri": "x-rincon-queue:test"})
        return p, SimpleNamespace(current=lambda: SimpleNamespace(coordinator=p))

    def test_shuffle_off_preserves_repeat_and_repeat_one(self):
        p, c = self.player()
        mode = PlaybackSettings(c)
        self.assertEqual(mode.adjust(-1), "Shuffle, off")
        self.assertTrue(p.repeat)
        mode.move(1)
        self.assertEqual(mode.adjust(1), "Repeat, one")
        self.assertEqual(p.repeat, "ONE")
        mode.move(1)
        self.assertEqual(mode.adjust(1), "Crossfade, on")

    def test_source_change_rejects_write(self):
        p, c = self.player()
        mode = PlaybackSettings(c)
        p.get_current_media_info = lambda: {"uri": "x-sonos-vli:spotify:session"}
        with self.assertRaisesRegex(RuntimeError, "Sonos queue"):
            mode.adjust(-1)
        self.assertTrue(p.shuffle)

    def test_ignored_write_is_reported(self):
        class Ignored:
            repeat = False
            cross_fade = False
            def get_current_media_info(self): return {"uri": "x-rincon-queue:test"}
            @property
            def shuffle(self): return False
            @shuffle.setter
            def shuffle(self, value): pass
        mode = PlaybackSettings(SimpleNamespace(current=lambda: SimpleNamespace(coordinator=Ignored())))
        with patch("sonosAddon.playbacksettings.time.sleep"), self.assertRaisesRegex(RuntimeError, "not applied"):
            mode.adjust(1)
