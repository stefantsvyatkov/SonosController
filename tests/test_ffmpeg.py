"""Finding FFmpeg on the machine, and what happens when there is none."""

import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import load

ffmpeg = load("ffmpeg")
relayhooks = load("relayhooks")

#: A real FFmpeg lives here in a development checkout; skipped without it.
BUNDLED = (Path(__file__).resolve().parents[1]
           / "experimental" / "ffmpeg-relay" / "bin" / "x64" / "ffmpeg.exe")


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

    @unittest.skipUnless(BUNDLED.is_file(), "no FFmpeg in this checkout")
    def test_the_real_binary_is_accepted(self):
        found = ffmpeg.describe(BUNDLED)
        self.assertIsNotNone(found, "the bundled FFmpeg was not recognised")
        self.assertIn("ffmpeg version", found[0].lower())

    @unittest.skipUnless(BUNDLED.is_file(), "no FFmpeg in this checkout")
    def test_a_path_given_as_a_string_works_too(self):
        self.assertIsNotNone(ffmpeg.describe(BUNDLED.as_posix()))


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
        self.assertIn("No FFmpeg", reason)
        self.assertEqual(encoders, set())


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

    def test_without_ffmpeg_a_refused_format_says_it_could_convert(self):
        relay = self.relay()
        refusal = RuntimeError("Audio format rejected by the speaker.")
        with patch.object(relayhooks, "_play_url", side_effect=refusal):
            with self.assertRaises(RuntimeError) as caught:
                relay.play_url("player", "http://x/a.ogg", bool, print)
        self.assertIn("FFmpeg", str(caught.exception))
        self.assertIn("none here", str(caught.exception))

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
