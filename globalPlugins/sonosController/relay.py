"""Bounded local relay used only when Sonos cannot play a URL directly."""

from collections import deque
import ctypes
from dataclasses import dataclass
import ipaddress
import logging
import os
import secrets
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


try:
    from ctypes import wintypes
except (ImportError, ValueError):  # not Windows; the tests still import this
    wintypes = None

log = logging.getLogger(__name__)
try:
    from logHandler import log as _nvdaLog
except ImportError:
    pass
else:
    log = _nvdaLog


MAX_CONNECTIONS = 2
PREBUFFER_BYTES = 8192
PROCESS_STOP_SECONDS = 2.0
USER_AGENT = "SonosController/0.1"


@dataclass(frozen=True)
class RelayMode:
    name: str
    content_type: str
    extension: str
    output_arguments: tuple


MODES = {
    "remux_mp3": RelayMode("remux_mp3", "audio/mpeg", "radio.mp3", ("-c:a", "copy", "-f", "mp3")),
    "remux_aac": RelayMode("remux_aac", "audio/aac", "radio.aac", ("-c:a", "copy", "-f", "adts")),
    "transcode_mp3": RelayMode(
        "transcode_mp3",
        "audio/mpeg",
        "radio.mp3",
        ("-c:a", "libmp3lame", "-b:a", "192k", "-ac", "2", "-ar", "44100", "-write_xing", "0", "-f", "mp3"),
    ),
}


def fallback_modes(source):
    """Return the useful fallback stages without retrying a known-bad container."""
    path = urlsplit(source.url).path.lower()
    mime = (source.mime or "").lower()
    if source.kind == "hls" or path.endswith(".m3u8"):
        return ("remux_aac", "transcode_mp3")
    if mime == "application/ogg" or path.endswith((".ogg", ".opus")):
        return ("transcode_mp3",)
    if mime in ("audio/mpeg", "audio/mp3") or path.endswith(".mp3"):
        return ("remux_mp3", "transcode_mp3")
    if mime in ("audio/aac", "audio/aacp") or path.endswith(".aac"):
        return ("remux_aac", "transcode_mp3")
    return ("transcode_mp3",)


def default_ffmpeg_path():
    """Only the personal build has a copy beside the add-on."""
    from .ffmpeg import bundled_path
    return bundled_path()


def ffmpeg_command(executable, source_url, mode):
    """Build a fixed argument vector; source_url always remains one argument."""
    parts = urlsplit(str(source_url))
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use an unauthenticated HTTP or HTTPS stream.")
    selected = MODES[mode]
    protocols = "https,tcp,tls,crypto,pipe" if parts.scheme.lower() == "https" else "http,https,tcp,tls,crypto,pipe"
    command = [
        str(executable), "-hide_banner", "-nostdin", "-loglevel", "warning", "-nostats",
        "-rw_timeout", "10000000",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
        "-reconnect_on_http_error", "5xx", "-reconnect_delay_max", "2",
        "-reconnect_max_retries", "3", "-reconnect_delay_total_max", "8",
        "-protocol_whitelist", protocols,
    ]
    if parts.scheme.lower() == "https":
        command.extend(("-tls_verify", "1"))
    command.extend((
        "-user_agent", USER_AGENT, "-i", str(source_url), "-map", "0:a:0",
        "-vn", "-sn", "-dn", "-threads", "1", "-flush_packets", "1",
    ))
    command.extend(selected.output_arguments)
    command.append("pipe:1")
    return command


def _valid_ipv4(value):
    try:
        return str(ipaddress.IPv4Address(str(value)))
    except ipaddress.AddressValueError:
        return None


class _WindowsJob:
    """Own child processes so Windows kills them if NVDA closes unexpectedly."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimits(ctypes.Structure):
        pass

    _ExtendedLimits._fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "Could not create the FFmpeg process job")
        limits = self._ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle,
            self._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "Could not configure the FFmpeg process job")
        self._kernel32 = kernel32
        self.handle = handle

    def assign(self, process):
        if self.handle is None:
            return
        if not self._kernel32.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            raise OSError(ctypes.get_last_error(), "Could not contain the FFmpeg process")

    def close(self):
        handle, self.handle = self.handle, None
        if handle is not None:
            self._kernel32.CloseHandle(handle)


class RelaySession:
    def __init__(self, manager, server, source, mode, clients):
        self.manager = manager
        self.server = server
        self.source = source
        self.mode = MODES[mode]
        self.clients = frozenset(filter(None, (_valid_ipv4(value) for value in clients)))
        self.path = "/" + secrets.token_urlsafe(24) + "/" + self.mode.extension
        self.lock = threading.RLock()
        self.processes = set()
        self.active = True
        self.requested = threading.Event()
        self.failed = threading.Event()
        self.logs = deque(maxlen=30)
        server.register(self)

    @property
    def url(self):
        return f"http://{self.server.address}:{self.server.port}{self.path}"

    @property
    def content_type(self):
        return self.mode.content_type

    def add_log(self, line):
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            self.logs.append(text[:2048])

    def add_process(self, process):
        with self.lock:
            if not self.active:
                raise RuntimeError("Relay session stopped.")
            self.processes.add(process)

    def discard_process(self, process):
        with self.lock:
            self.processes.discard(process)

    def close(self):
        with self.lock:
            if not self.active:
                return
            self.active = False
            processes = list(self.processes)
        self.server.unregister(self)
        for process in processes:
            self.manager.terminate_process(process)


class RelayServer:
    def __init__(self, manager, address):
        self.manager = manager
        self.address = address
        self.routes = {}
        self.lock = threading.RLock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_args):
                pass

            def do_HEAD(self):
                self._serve(False)

            def do_GET(self):
                self._serve(True)

            def do_POST(self):
                self.send_error(405)

            def _serve(self, body):
                with owner.lock:
                    session = owner.routes.get(self.path)
                client = _valid_ipv4(self.client_address[0])
                expected_host = f"{owner.address}:{owner.port}"
                if session is None or not session.active or client not in session.clients:
                    self.send_error(404)
                    return
                if self.headers.get("Host", "") != expected_host:
                    self.send_error(400)
                    return
                if not body:
                    self.send_response(200)
                    self.send_header("Content-Type", session.content_type)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    return
                if not owner.manager.connection_gate.acquire(blocking=False):
                    self.send_error(503)
                    return
                process = None
                try:
                    process = owner.manager.start_process(session)
                    session.requested.set()
                    first = process.stdout.read(PREBUFFER_BYTES)
                    if not first:
                        session.failed.set()
                        self.send_error(502)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", session.content_type)
                    self.send_header("icy-name", " ".join(session.source.title.split()).encode("latin-1", errors="replace").decode("latin-1"))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    chunk = first
                    while chunk and session.active:
                        self.wfile.write(chunk)
                        chunk = process.stdout.read(32768)
                    if session.active and not chunk:
                        session.failed.set()
                except (BrokenPipeError, ConnectionError, OSError, ValueError):
                    pass
                except Exception:
                    session.failed.set()
                    log.exception("Sonos relay request failed")
                finally:
                    if process is not None:
                        owner.manager.terminate_process(process)
                        session.discard_process(process)
                    owner.manager.connection_gate.release()

        self.httpd = ThreadingHTTPServer((address, 0), Handler)
        self.httpd.daemon_threads = True
        self.httpd.block_on_close = False
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="SonosRelayHTTP", daemon=True)
        self.thread.start()

    def register(self, session):
        with self.lock:
            self.routes[session.path] = session

    def unregister(self, session):
        with self.lock:
            if self.routes.get(session.path) is session:
                self.routes.pop(session.path, None)

    def close(self):
        with self.lock:
            sessions = list(self.routes.values())
            self.routes.clear()
        for session in sessions:
            session.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=1.5)


class RelayManager:
    def __init__(self, executable=None, popen=None, job_factory=None):
        from pathlib import Path
        self.executable = Path(executable) if executable is not None else default_ffmpeg_path()
        self.popen = popen or subprocess.Popen
        self.job_factory = job_factory or _WindowsJob
        self.job = None
        self.servers = {}
        self.current = None
        self.lock = threading.RLock()
        self.connection_gate = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.closed = False

    @property
    def available(self):
        return self.executable is not None and self.executable.is_file()

    @staticmethod
    def bind_address(player):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect((player.ip_address, 1400))
            return route.getsockname()[0]

    @staticmethod
    def clients(player, extra=()):
        values = [player.ip_address]
        try:
            values.extend(member.ip_address for member in player.group.members)
        except Exception:
            pass
        values.extend(extra)
        return tuple(filter(None, (_valid_ipv4(value) for value in values)))

    def _job(self):
        with self.lock:
            if self.closed:
                raise RuntimeError("Local audio fallback stopped.")
            if self.job is None:
                self.job = self.job_factory()
            return self.job

    def start_process(self, session):
        if not session.active or self.closed:
            raise RuntimeError("Relay session stopped.")
        command = ffmpeg_command(self.executable, session.source.url, session.mode.name)
        process = self.popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            self._job().assign(process)
            session.add_process(process)
        except Exception:
            self.terminate_process(process)
            raise

        def drain():
            try:
                for block in iter(lambda: process.stderr.read(4096), b""):
                    session.add_log(block)
            except (OSError, ValueError):
                pass

        threading.Thread(target=drain, name="SonosRelayLog", daemon=True).start()
        return process

    @staticmethod
    def terminate_process(process):
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=PROCESS_STOP_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
        except (OSError, subprocess.SubprocessError):
            pass
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def start(self, player, source, mode, clients=()):
        if mode not in MODES:
            raise ValueError("Unknown relay mode.")
        if not self.available:
            raise RuntimeError("Local audio fallback is unavailable.")
        address = self.bind_address(player)
        allowed = self.clients(player, clients)
        with self.lock:
            if self.closed:
                raise RuntimeError("Local audio fallback stopped.")
            previous = self.current
            if address not in self.servers:
                self.servers[address] = RelayServer(self, address)
            session = RelaySession(self, self.servers[address], source, mode, allowed)
            self.current = session
        if previous is not None:
            previous.close()
        return session

    def stop(self, session=None):
        with self.lock:
            current = self.current
            if session is not None and current is not session:
                target = session
            else:
                target = current
                if target is not None:
                    self.current = None
        if target is not None:
            target.close()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            current, self.current = self.current, None
            servers = list(self.servers.values())
            self.servers.clear()
            job, self.job = self.job, None
        if current is not None:
            current.close()
        for server in servers:
            server.close()
        if job is not None:
            job.close()
