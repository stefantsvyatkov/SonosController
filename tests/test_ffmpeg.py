"""Finding FFmpeg, downloading one, and what happens when there is none."""

import hashlib
import http.server
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import load

ffmpeg = load("ffmpeg")
relayhooks = load("relayhooks")

def result(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


VERSION = "ffmpeg version 7.1 Copyright (c) 2000-2024\nconfiguration: --enable-libmp3lame\n"


class Describe(unittest.TestCase):
    def test_a_real_ffmpeg_identifies_itself(self):
        found = ffmpeg.describe("anything", run=lambda *a, **k: result(stdout=VERSION))
        version, encoders = found
        self.assertTrue(version.startswith("ffmpeg version 7.1"))
        self.assertIn("libmp3lame", encoders)

    def test_something_that_is_not_ffmpeg_is_rejected(self):
        other = "Microsoft Windows [Version 10]"
        self.assertIsNone(ffmpeg.describe("x", run=lambda *a, **k: result(stdout=other)))

    def test_a_failing_executable_is_rejected(self):
        self.assertIsNone(
            ffmpeg.describe("x", run=lambda *a, **k: result(returncode=1, stdout=VERSION)))

    def test_a_missing_executable_is_rejected(self):
        def explode(*a, **k):
            raise OSError("not found")

        self.assertIsNone(ffmpeg.describe("x", run=explode))

    def test_a_hanging_executable_is_rejected(self):
        def hang(*a, **k):
            raise subprocess.TimeoutExpired("ffmpeg", ffmpeg.PROBE_TIMEOUT)

        self.assertIsNone(ffmpeg.describe("x", run=hang))

    def test_a_path_given_as_a_string_works_too(self):
        found = ffmpeg.describe("C:/x/ffmpeg.exe", run=lambda *a, **k: result(stdout=VERSION))
        self.assertIsNotNone(found)


class Find(unittest.TestCase):
    def test_path_is_searched(self):
        with patch.object(ffmpeg.shutil, "which", return_value="C:/tools/ffmpeg.exe"):
            names = [p.as_posix() for p in ffmpeg.candidates()]
        self.assertIn("C:/tools/ffmpeg.exe", names)

    def test_a_configured_path_is_tried_first(self):
        names = [p.as_posix() for p in ffmpeg.candidates("D:/mine/ffmpeg.exe")]
        self.assertEqual(names[0], "D:/mine/ffmpeg.exe")

    def test_a_configured_path_that_is_missing_is_reported(self):
        path, reason, _encoders = ffmpeg.find("D:/nowhere/ffmpeg.exe")
        self.assertIsNone(path)
        self.assertIn("does not exist", reason)

    def test_nothing_found_is_explained(self):
        with patch.object(ffmpeg, "candidates", return_value=[]):
            path, reason, encoders = ffmpeg.find()
        self.assertIsNone(path)
        self.assertIn("not installed", reason)
        self.assertEqual(encoders, set())


class Install(unittest.TestCase):
    """The download: served locally, so nothing here touches the network."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.target = root / "ffmpeg"
        self.archive = self.release(root / "release.zip")
        self.served = self.archive.read_bytes()
        self.digest = hashlib.sha256(self.served).hexdigest()
        self.server, self.address = self.serve()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def release(self, path):
        """An archive shaped like the real one, with stand-ins inside."""
        root = next(iter(ffmpeg.MEMBERS.values())).split("/", 1)[0]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"{root}/bin/ffmpeg.exe", b"MZ not really ffmpeg")
            archive.writestr(f"{root}/LICENSE", "the licence text")
            archive.writestr(f"{root}/bin/ffprobe.exe", b"MZ not wanted")
        return path

    def serve(self):
        body = self.served

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(handler):
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)

            def log_message(handler, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_port}/ffmpeg.zip"

    def pinned(self, digest=None):
        return patch.multiple(ffmpeg, URL=self.address, SHA256=digest or self.digest,
                              SIZE=len(self.served), folder=lambda: self.target)

    def test_a_good_download_is_unpacked(self):
        seen = []
        with self.pinned():
            path = ffmpeg.install(progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(path, self.target / "ffmpeg.exe")
        self.assertEqual(path.read_bytes(), b"MZ not really ffmpeg")
        self.assertEqual((self.target / "LICENSE.txt").read_text(encoding="utf-8"),
                         "the licence text")
        # Only what MEMBERS asks for: ffprobe is in the archive but not wanted.
        self.assertEqual(sorted(p.name for p in self.target.iterdir()),
                         ["LICENSE.txt", "ffmpeg.exe"])
        self.assertTrue(seen, "progress was never reported")
        self.assertEqual(seen[-1][0], len(self.served))

    def test_a_tampered_download_is_refused_and_leaves_nothing(self):
        with self.pinned(digest="0" * 64):
            with self.assertRaises(ValueError):
                ffmpeg.install()
        self.assertFalse(self.target.exists(), "a refused download must not be installed")
        self.assertFalse(self.target.with_name(self.target.name + ".download").exists())

    def test_cancelling_leaves_nothing(self):
        with self.pinned():
            with self.assertRaises(ffmpeg.Cancelled):
                ffmpeg.install(cancelled=lambda: True)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_name(self.target.name + ".download").exists())

    def test_installing_again_replaces_what_was_there(self):
        self.target.mkdir(parents=True)
        (self.target / "stale.txt").write_text("from an older version", encoding="utf-8")
        with self.pinned():
            ffmpeg.install()
        self.assertFalse((self.target / "stale.txt").exists())
        self.assertTrue((self.target / "ffmpeg.exe").is_file())

    def test_a_downloaded_copy_is_offered_to_the_finder(self):
        self.target.mkdir(parents=True)
        (self.target / "ffmpeg.exe").write_bytes(b"MZ")
        with patch.object(ffmpeg, "folder", lambda: self.target):
            with patch.object(ffmpeg.shutil, "which", return_value=None):
                names = [p.as_posix() for p in ffmpeg.candidates()]
        self.assertEqual(names, [(self.target / "ffmpeg.exe").as_posix()])

    def test_an_ffmpeg_on_path_is_preferred_to_downloading_another(self):
        self.target.mkdir(parents=True)
        (self.target / "ffmpeg.exe").write_bytes(b"MZ")
        with patch.object(ffmpeg, "folder", lambda: self.target):
            with patch.object(ffmpeg.shutil, "which", return_value="C:/tools/ffmpeg.exe"):
                names = [p.as_posix() for p in ffmpeg.candidates()]
        self.assertEqual(names[0], "C:/tools/ffmpeg.exe")


class Fallback(unittest.TestCase):
    """The layer's view: conversion only exists when FFmpeg does."""

    def relay(self, path=None):
        found = (path, "ffmpeg version 7.1", {"libmp3lame"}) if path else (None, "none here", set())
        return relayhooks.Relay(finder=lambda configured: found)

    def test_without_ffmpeg_the_url_plays_directly(self):
        relay = self.relay()
        self.assertFalse(relay.available)
        with patch.object(relayhooks, "_play_url", return_value="Playing Radio") as direct:
            self.assertEqual(relay.play_url("player", "http://x/a.mp3", bool, print), "Playing Radio")
        direct.assert_called_once()

    def test_without_ffmpeg_a_refused_format_asks_for_the_download(self):
        relay = self.relay()
        refusal = RuntimeError("Audio format rejected by the speaker.")
        with patch.object(relayhooks, "_play_url", side_effect=refusal):
            with self.assertRaises(relayhooks.NeedsFFmpeg) as caught:
                relay.play_url("player", "http://x/a.ogg", bool, print)
        # The speaker's own complaint travels with the signal, so whoever
        # offers the download can say what went wrong.
        self.assertIs(caught.exception.problem, refusal)

    def test_without_ffmpeg_an_unrelated_failure_is_passed_through(self):
        relay = self.relay()
        with patch.object(relayhooks, "_play_url", side_effect=RuntimeError("No route to host")):
            with self.assertRaises(RuntimeError) as caught:
                relay.play_url("player", "http://x/a.mp3", bool, print)
        self.assertEqual(str(caught.exception), "No route to host")

    def test_the_probe_runs_once(self):
        calls = []

        def finder(configured):
            calls.append(configured)
            return None, "none here", set()

        relay = relayhooks.Relay(finder=finder)
        for _ in range(5):
            relay.available
        self.assertEqual(len(calls), 1, "the machine should be searched once, then remembered")
        relay.recheck()
        relay.available
        self.assertEqual(len(calls), 2, "recheck must look again")

    def test_stopping_an_unused_relay_is_harmless(self):
        relay = self.relay()
        relay.stop()
        relay.close()


if __name__ == "__main__":
    unittest.main()
