"""Production relay lifecycle and boundary tests without external network access."""

from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import http.client
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import load

relay = load("relay")


class FakeJob:
    def __init__(self):
        self.assigned = []
        self.closed = False

    def assign(self, process):
        self.assigned.append(process)

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, output=b"\xff\xfb" * 8192):
        self.stdout = BytesIO(output)
        self.stderr = BytesIO(b"")
        self.returncode = None
        self.terminated = False
        self.killed = False
        self._handle = 1

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -1

    def wait(self, timeout=None):
        return self.returncode


class Player:
    def __init__(self, address="127.0.0.1"):
        self.ip_address = address
        self.group = types.SimpleNamespace(members=[self], coordinator=self)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.binary = Path(self.temp.name) / "ffmpeg.exe"
        self.binary.write_bytes(b"test")
        self.processes = []
        self.jobs = []

        def popen(_command, **_kwargs):
            process = FakeProcess()
            self.processes.append(process)
            return process

        def job_factory():
            job = FakeJob()
            self.jobs.append(job)
            return job

        self.manager = relay.RelayManager(self.binary, popen=popen, job_factory=job_factory)
        self.source = types.SimpleNamespace(url="https://example.com/live.ogg", mime="application/ogg", kind="radio", title="Station")

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def test_command_is_fixed_and_url_is_one_argument(self):
        address = "https://example.com/live?x=1&next=calc.exe"
        command = relay.ffmpeg_command(self.binary, address, "transcode_mp3")
        self.assertEqual(command.count(address), 1)
        self.assertIn("libmp3lame", command)
        self.assertEqual(command[command.index("-protocol_whitelist") + 1], "https,tcp,tls,crypto,pipe")
        self.assertNotIn("file", command[command.index("-protocol_whitelist") + 1].split(","))
        self.assertIn("-tls_verify", command)
        http_command = relay.ffmpeg_command(self.binary, "http://example.com/live", "transcode_mp3")
        self.assertEqual(
            http_command[http_command.index("-protocol_whitelist") + 1],
            "http,https,tcp,tls,crypto,pipe",
        )
        with self.assertRaises(ValueError):
            relay.ffmpeg_command(self.binary, "http://user:secret@example.com/live", "transcode_mp3")

    def test_mode_planner_avoids_useless_ogg_remux(self):
        self.assertEqual(relay.fallback_modes(self.source), ("transcode_mp3",))
        hls = types.SimpleNamespace(url="https://example.com/live.m3u8", mime="application/vnd.apple.mpegurl", kind="hls")
        self.assertEqual(relay.fallback_modes(hls), ("remux_aac", "transcode_mp3"))
        mp3 = types.SimpleNamespace(url="http://example.com/live", mime="audio/mpeg", kind="radio")
        self.assertEqual(relay.fallback_modes(mp3), ("remux_mp3", "transcode_mp3"))

    def test_endpoint_scope_head_get_and_cleanup(self):
        session = self.manager.start(Player(), self.source, "transcode_mp3")
        with urlopen(Request(session.url, method="HEAD"), timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "audio/mpeg")
        self.assertFalse(self.processes)
        with urlopen(session.url, timeout=2) as response:
            self.assertTrue(response.read(4))
        self.assertTrue(session.requested.is_set())
        deadline = time.monotonic() + 1
        while not self.processes[0].terminated and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.processes[0].terminated)
        with self.assertRaises(HTTPError) as error:
            urlopen(session.url + "?url=http://other", timeout=2)
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        self.manager.stop(session)
        with self.assertRaises(HTTPError) as error:
            urlopen(session.url, timeout=2)
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_wrong_host_and_post_are_rejected(self):
        session = self.manager.start(Player(), self.source, "transcode_mp3")
        connection = http.client.HTTPConnection(session.server.address, session.server.port, timeout=2)
        connection.putrequest("GET", session.path, skip_host=True)
        connection.putheader("Host", "127.0.0.1:1")
        connection.endheaders()
        self.assertEqual(connection.getresponse().status, 400)
        connection.close()
        request = Request(session.url, data=b"", method="POST")
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=2)
        self.assertEqual(error.exception.code, 405)
        error.exception.close()

    def test_empty_encoder_output_is_a_fast_failure(self):
        self.manager.popen = lambda *_args, **_kwargs: FakeProcess(b"")
        session = self.manager.start(Player(), self.source, "transcode_mp3")
        with self.assertRaises(HTTPError) as error:
            urlopen(session.url, timeout=2)
        self.assertEqual(error.exception.code, 502)
        error.exception.close()
        self.assertTrue(session.failed.wait(1))

    def test_close_is_idempotent_and_closes_job(self):
        session = self.manager.start(Player(), self.source, "transcode_mp3")
        process = self.manager.start_process(session)
        self.manager.close()
        self.manager.close()
        self.assertTrue(process.terminated)
        self.assertTrue(self.jobs[0].closed)
        self.assertFalse(self.manager.servers)

    def test_source_switch_revokes_old_route_and_stops_old_process(self):
        first = self.manager.start(Player(), self.source, "transcode_mp3")
        process = self.manager.start_process(first)
        second_source = types.SimpleNamespace(
            url="https://example.com/next.aac", mime="audio/aac", kind="radio", title="Next"
        )
        second = self.manager.start(Player(), second_source, "remux_aac")
        self.assertFalse(first.active)
        self.assertTrue(process.terminated)
        self.assertIs(self.manager.current, second)
        with self.assertRaises(HTTPError) as error:
            urlopen(first.url, timeout=2)
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_concurrent_starts_leave_one_current_session(self):
        def start(index):
            source = types.SimpleNamespace(
                url=f"https://example.com/{index}.ogg", mime="application/ogg", kind="radio", title=str(index)
            )
            return self.manager.start(Player(), source, "transcode_mp3")

        with ThreadPoolExecutor(max_workers=8) as pool:
            sessions = list(pool.map(start, range(32)))
        active = [session for session in sessions if session.active]
        self.assertEqual(active, [self.manager.current])
        self.assertEqual(len(self.manager.servers), 1)

    def test_connection_limit_rejects_extra_request_without_process(self):
        session = self.manager.start(Player(), self.source, "transcode_mp3")
        self.assertTrue(self.manager.connection_gate.acquire(blocking=False))
        self.assertTrue(self.manager.connection_gate.acquire(blocking=False))
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(session.url, timeout=2)
            self.assertEqual(error.exception.code, 503)
            error.exception.close()
            self.assertFalse(self.processes)
        finally:
            self.manager.connection_gate.release()
            self.manager.connection_gate.release()

    @unittest.skipUnless(os.name == "nt", "Windows Job Objects are Windows-only")
    def test_real_job_shutdown_terminates_child(self):
        children = []

        def popen(_command, **kwargs):
            process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                **kwargs,
            )
            children.append(process)
            return process

        manager = relay.RelayManager(self.binary, popen=popen)
        try:
            session = manager.start(Player(), self.source, "transcode_mp3")
            process = manager.start_process(session)
            self.assertIsNone(process.poll())
            manager.close()
            process.wait(timeout=3)
            self.assertIsNotNone(process.returncode)
        finally:
            manager.close()
            for process in children:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
