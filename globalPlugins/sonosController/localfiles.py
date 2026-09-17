"""Serve explicitly selected audio files to Sonos over the local network."""

import os
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, quote

from .i18n import _
from . import vendors

#: Refuse folders bigger than this; each batch of 16 is one speaker request.
MAX_FILES = 1000
#: Tracks per AddMultipleURIsToQueue request, as SoCo chunks them.
QUEUE_BATCH = 16

MIME = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".wav": "audio/wav",
        ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "application/ogg",
        ".wma": "audio/x-ms-wma", ".aif": "audio/aiff", ".aiff": "audio/aiff"}


def audio_files(path, recursive=False):
    path = os.path.realpath(path)
    if os.path.isfile(path):
        files = [path]
    elif os.path.isdir(path):
        files = []
        for directory, dirs, names in os.walk(path, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(directory, d))) if recursive else []
            for name in names:
                candidate = os.path.join(directory, name)
                if not os.path.islink(candidate) and os.path.splitext(name)[1].lower() in MIME:
                    if len(files) >= MAX_FILES:
                        raise RuntimeError(
                            _("Select a folder with at most {count} audio files.").format(
                                count=MAX_FILES))
                    files.append(candidate)
    else:
        raise RuntimeError(_("File or folder not found."))
    files = [f for f in files if os.path.splitext(f)[1].lower() in MIME and os.path.getsize(f) > 0]
    if not files:
        raise RuntimeError(_("No supported audio files."))
    def order(filename):
        return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                     for part in re.split(r"(\d+)", os.path.relpath(filename, path)))
    return sorted(files, key=order)


def byte_range(header, size):
    if header is None:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or not any(match.groups()) or size == 0:
        raise ValueError("Invalid range")
    start, end = match.groups()
    if not start:
        length = int(end)
        if length <= 0:
            raise ValueError("Invalid suffix")
        return max(0, size - length), size - 1, True
    start = int(start)
    end = min(int(end), size - 1) if end else size - 1
    if start >= size or start > end:
        raise ValueError("Unsatisfiable range")
    return start, end, True


class AudioServer:
    def __init__(self, address):
        self.files = {}
        self.lock = threading.Lock()
        self.accessed = set()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(10)
            def log_message(self, *args):
                pass
            def do_HEAD(self):
                self.serve(False)
            def do_GET(self):
                self.serve(True)
            def serve(self, body):
                with owner.lock:
                    entry = owner.files.get(urlsplit(self.path).path)
                if entry is None:
                    self.send_error(404)
                    return
                filename, mime = entry
                try:
                    file = open(filename, "rb")
                except OSError:
                    self.send_error(404)
                    return
                with file:
                    size = os.fstat(file.fileno()).st_size
                    try:
                        start, end, partial = byte_range(self.headers.get("Range"), size)
                    except ValueError:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(206 if partial else 200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(max(0, end - start + 1)))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Connection", "close")
                    if partial:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.end_headers()
                    if body:
                        with owner.lock:
                            owner.accessed.add(urlsplit(self.path).path)
                        file.seek(start)
                        left = end - start + 1
                        try:
                            while left > 0:
                                data = file.read(min(left, 65536))
                                if not data:
                                    break
                                self.wfile.write(data)
                                left -= len(data)
                        except (OSError, ConnectionError):
                            pass
        self.server = ThreadingHTTPServer((address, 0), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.thread = threading.Thread(target=self.server.serve_forever, name="SonosAudioHTTP", daemon=True)
        self.thread.start()

    def forget_all(self):
        """Stop serving everything queued earlier.

        Routes used to accumulate for the whole NVDA session, which both grew
        without bound and kept old files reachable long after playback.
        """
        with self.lock:
            self.files.clear()
            self.accessed.clear()

    def register(self, filename):
        filename = os.path.realpath(filename)
        extension = os.path.splitext(filename)[1].lower()
        if extension not in MIME or not os.path.isfile(filename):
            raise RuntimeError(_("Unsupported audio file."))
        route = "/" + secrets.token_urlsafe(24) + "/" + quote(os.path.basename(filename), safe="")
        with self.lock:
            self.files[route] = (filename, MIME[extension])
        address, port = self.server.server_address
        return f"http://{address}:{port}{route}", MIME[extension]

    def was_requested(self, url):
        with self.lock:
            return urlsplit(url).path in self.accessed

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


class LocalFiles:
    def __init__(self):
        self.servers = {}
        self.lock = threading.Lock()
        self.closed = False

    def close(self):
        with self.lock:
            self.closed = True
            servers = list(self.servers.values())
            self.servers.clear()
        for server in servers:
            server.close()

    def server_for(self, player):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect((player.ip_address, 1400))
            address = route.getsockname()[0]
        with self.lock:
            if self.closed:
                raise RuntimeError(_("Local playback stopped."))
            if address not in self.servers:
                self.servers[address] = AudioServer(address)
            return self.servers[address]

    def play(self, player, selection, cancelled=lambda: False):
        structures = vendors.soco().data_structures
        DidlMusicTrack = structures.DidlMusicTrack
        DidlResource = structures.DidlResource
        paths = audio_files(selection["path"], selection.get("recursive", False))
        if cancelled():
            return ""
        player = player.group.coordinator
        server = self.server_for(player)
        server.forget_all()
        items = []
        for path in paths:
            url, mime = server.register(path)
            items.append(DidlMusicTrack(title=os.path.splitext(os.path.basename(path))[0], parent_id="0", item_id=url,
                resources=[DidlResource(uri=url, protocol_info=f"http-get:*:{mime}:*")]))
        added = 0
        try:
            if cancelled():
                return ""
            player.clear_queue()
            # AddMultipleURIsToQueue takes sixteen tracks per request, so a
            # large folder costs a handful of requests instead of one each.
            for start in range(0, len(items), QUEUE_BATCH):
                if cancelled():
                    return ""
                batch = items[start:start + QUEUE_BATCH]
                player.add_multiple_to_queue(batch)
                added += len(batch)
            if cancelled():
                return ""
            player.play_from_queue(0)
            for attempt in range(8):
                if cancelled():
                    return ""
                state = player.get_current_transport_info()
                if state.get("current_transport_state") == "PLAYING" and server.was_requested(items[0].resources[0].uri):
                    return _("Playing, {count} files.").format(count=len(items))
                time.sleep(0.5)
            return _("Files queued. Playback not confirmed; "
                     "check the firewall and audio format.")
        except Exception as error:
            blocked = added and not server.was_requested(items[0].resources[0].uri)
            hint = _(" Check NVDA firewall access.") if blocked else ""
            raise RuntimeError(
                _("Local playback failed.{hint} {count} files added to the queue.").format(
                    hint=hint, count=added)) from error
