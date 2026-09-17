"""Bounded URL inspection and source-aware local Sonos playback."""
from dataclasses import dataclass
import os
import time
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote
from xml.etree import ElementTree as ET
from .i18n import _
from .playback import source_identity
from .util import transient, valid_url
from . import vendors


PLAYLIST_MIME = {"audio/x-scpls", "audio/mpegurl", "audio/x-mpegurl", "application/x-mpegurl", "application/vnd.apple.mpegurl"}
FILE_MIME = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "application/ogg", ".wav": "audio/wav",
             ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac", ".wma": "audio/x-ms-wma",
             ".aif": "audio/aiff", ".aiff": "audio/aiff"}


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class AudioSource:
    url: str
    kind: str
    mime: str
    title: str


def clean_url(url):
    url = str(url or "").strip()
    if len(url) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in url) or not valid_url(url):
        raise RuntimeError(_("Enter an HTTP or HTTPS audio URL."))
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as error:
        raise RuntimeError(_("Invalid URL port.")) from error
    if port is not None and not 1 <= port <= 65535:
        raise RuntimeError(_("Invalid URL port."))
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""))


def resolve_audio(url, get=None, cancelled=lambda: False):
    if cancelled():
        raise Cancelled()
    if get is None:
        get = vendors.requests().get
    deadline = time.monotonic() + 20
    budget = [10]

    def check():
        if cancelled():
            raise Cancelled()
        if time.monotonic() >= deadline or budget[0] <= 0:
            raise RuntimeError(_("Audio address took too long to resolve."))

    def resolve(address, depth, visited):
        check()
        address = clean_url(address)
        if address in visited or depth > 4:
            raise RuntimeError(_("Playlist or redirect loop."))
        visited = visited | {address}
        budget[0] -= 1
        with get(address, stream=True, allow_redirects=False, timeout=(3, 4),
                 headers={"User-Agent": "SonosController/0.1", "Accept-Encoding": "identity"}) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            status = response.status_code
            if status in (301, 302, 303, 307, 308):
                location = headers.get("location")
                if not location:
                    raise RuntimeError(_("Redirect has no address."))
                target = clean_url(urljoin(address, location))
                if urlsplit(address).scheme == "https" and urlsplit(target).scheme != "https":
                    raise RuntimeError(_("Insecure HTTPS redirect rejected."))
                return resolve(target, depth + 1, visited)
            if status in (401, 403):
                raise RuntimeError(_("This audio address requires access permission."))
            response.raise_for_status()
            mime = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            path = urlsplit(address).path.lower()
            extension = os.path.splitext(path)[1]
            if mime in ("text/html", "application/xhtml+xml"):
                raise RuntimeError(_("This is a web page. Use a direct audio URL."))
            playlist = mime in PLAYLIST_MIME or extension in (".m3u", ".m3u8", ".pls")
            if playlist:
                data = bytearray()
                for chunk in response.iter_content(4096):
                    check()
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise RuntimeError(_("Playlist is too large."))
                text = data.decode("utf-8-sig", errors="replace")
                if "#EXT-X-" in text:
                    return AudioSource(address, "hls", "application/vnd.apple.mpegurl", _("Internet audio"))
                entries = []
                pls = extension == ".pls" or "[playlist]" in text.lower() or mime == "audio/x-scpls"
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith(("#", "[")):
                        continue
                    if pls:
                        key, sep, value = line.partition("=")
                        if not sep or not key.lower().startswith("file") or not key[4:].isdigit():
                            continue
                        line = value.strip()
                    target = urljoin(address, line)
                    if valid_url(target):
                        if urlsplit(address).scheme == "https" and urlsplit(target).scheme != "https":
                            continue
                        entries.append(target)
                # Continuous Ogg streams can be reachable but rejected by Sonos.
                # Prefer explicit MP3/AAC alternatives while preserving ties and
                # retaining Ogg as a last choice when no preferred entry resolves.
                def priority(target):
                    extension = os.path.splitext(urlsplit(target).path.lower())[1]
                    return 2 if extension in (".ogg", ".opus") else 0 if extension in (".mp3", ".aac", ".m3u8") else 1
                entries.sort(key=priority)
                last_error = None
                for target in entries[:8]:
                    try:
                        return resolve(target, depth + 1, visited)
                    except Cancelled:
                        raise
                    except Exception as error:
                        last_error = error
                raise RuntimeError(_("Playlist has no reachable audio address.")) from last_error
            if mime and not (mime.startswith("audio/") or mime in ("application/ogg", "application/octet-stream", "binary/octet-stream")):
                raise RuntimeError(_("The address does not return audio."))
            # Unknown MIME types need a small bounded sniff, not a whole stream download.
            if mime in ("", "application/octet-stream", "binary/octet-stream", "application/ogg"):
                sample = next(response.iter_content(512), b"")
                check()
                if sample.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    raise RuntimeError(_("This is a web page. Use a direct audio URL."))
                if b"OpusHead" in sample:
                    mime = "audio/opus"
                elif sample.startswith(b"OggS"):
                    mime = "application/ogg"
                elif sample.startswith(b"fLaC"):
                    mime = "audio/flac"
                elif sample.startswith(b"RIFF") and sample[8:12] == b"WAVE":
                    mime = "audio/wav"
                elif sample.startswith(b"ID3"):
                    mime = "audio/mpeg"
                elif mime != "application/ogg":
                    mime = FILE_MIME.get(extension, "audio/mpeg")
            radio = any(k.startswith("icy-") for k in headers) or (mime.startswith("audio/") and "content-length" not in headers)
            title = headers.get("icy-name", "").strip() or unquote(os.path.basename(urlsplit(address).path)) or _("Internet audio")
            title = " ".join(title.split())[:160]
            return AudioSource(address, "radio" if radio else "file", mime or FILE_MIME.get(extension, "audio/mpeg"), title)

    return resolve(url, 0, set())


def transport_candidates(source):
    # The legacy radio wrapper is only used for HTTP; converting HTTPS would
    # discard its transport scheme. HLS must retain its original manifest URI.
    if source.kind == "radio" and urlsplit(source.url).scheme == "http":
        return ["x-rincon-mp3radio:" + source.url.split(":", 1)[1], source.url]
    if source.kind == "file" and source.mime == "audio/mpeg" and urlsplit(source.url).scheme == "http":
        return [source.url, "x-rincon-mp3radio:" + source.url.split(":", 1)[1]]
    return [source.url]


def metadata(source, uri):
    didl = "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
    dc = "http://purl.org/dc/elements/1.1/"
    upnp = "urn:schemas-upnp-org:metadata-1-0/upnp/"
    root = ET.Element("DIDL-Lite", xmlns=didl)
    item = ET.SubElement(root, "item", id="-1", parentID="-1", restricted="true")
    ET.SubElement(item, "{" + dc + "}title").text = source.title
    ET.SubElement(item, "{" + upnp + "}class").text = "object.item.audioItem.audioBroadcast" if source.kind in ("radio", "hls") else "object.item.audioItem.musicTrack"
    if uri.startswith("x-rincon-mp3radio:"):
        # Match the metadata shape used by SoCo for the legacy radio transport.
        ET.SubElement(item, "desc", id="cdudn", nameSpace="urn:schemas-rinconnetworks-com:metadata-1-0/").text = "SA_RINCON65031_"
    else:
        ET.SubElement(item, "res", protocolInfo=f"http-get:*:{source.mime}:*").text = source.url
    return ET.tostring(root, encoding="unicode")


def _confirmed_start(player, uri, meta, cancelled, progress, abort=None):
    def matches():
        return source_identity(player.get_current_media_info().get("uri", "")) == source_identity(uri)
    if cancelled():
        raise Cancelled()
    try:
        player.play_uri(uri, meta=meta, start=False)
    except Exception as error:
        if not transient(error) or not matches():
            raise
    if cancelled():
        raise Cancelled()
    pending = None
    try:
        player.play()
    except Exception as error:
        if not transient(error):
            raise
        pending = error
    progress(_("Starting."))
    # Acknowledgement of Play is not confirmation of decoding. Allow radio
    # buffering, and never reset the URI while the device is transitioning.
    deadline = time.monotonic() + 12
    retries = 0
    playing = 0
    while time.monotonic() < deadline:
        if cancelled():
            raise Cancelled()
        if abort is not None and abort():
            raise RuntimeError(_("Local audio fallback failed."))
        try:
            state = player.get_current_transport_info()
            if not matches():
                raise RuntimeError(_("Playback source changed."))
            status = state.get("current_transport_status", "OK")
            current = state.get("current_transport_state")
            if status not in ("", "OK", None):
                raise RuntimeError(_("The speaker rejected the stream."))
            if current == "PLAYING":
                playing += 1
                if playing >= 2:
                    return True
            else:
                playing = 0
            if (current in ("STOPPED", "PAUSED_PLAYBACK") and pending is not None
                    and str(getattr(pending, "error_code", "")) == "701" and retries < 2):
                retries += 1
                time.sleep(0.4)
                if cancelled():
                    raise Cancelled()
                try:
                    player.play()
                    pending = None
                except Exception as error:
                    if not transient(error):
                        raise
                    pending = error
        except Cancelled:
            raise
        except Exception as error:
            if not transient(error):
                raise
            pending = error
        time.sleep(0.4)
    raise RuntimeError(NOT_STARTED) from pending


NOT_STARTED = _("The speaker did not start the stream.")


def _raise_direct_error(source, error):
    code = str(getattr(error, "error_code", ""))
    if code in ("704", "714"):
        raise RuntimeError(_("Audio format rejected by the speaker.")) from error
    if source.mime == "application/ogg" and str(error) == NOT_STARTED:
        raise RuntimeError(_(
            "The speaker did not start this Ogg stream. "
            "Try the station's MP3 or AAC address.")) from error
    raise error


def _direct(player, source, cancelled, progress):
    last_error = None
    candidates = transport_candidates(source)
    for index, uri in enumerate(candidates):
        try:
            _confirmed_start(player, uri, metadata(source, uri), cancelled, progress)
            return True
        except Cancelled:
            raise
        except Exception as error:
            last_error = error
            code = str(getattr(error, "error_code", ""))
            if code in ("714", "704") and index + 1 < len(candidates):
                continue
            raise
    if last_error is not None:
        raise last_error
    return False


def play_url(player, url, cancelled=lambda: False, progress=lambda message: None):
    try:
        source = resolve_audio(url, cancelled=cancelled)
        if cancelled():
            return ""
        player = player.group.coordinator
        if source.mime == "audio/opus":
            raise RuntimeError(_("Opus is not supported by direct Sonos URL playback."))
        try:
            if _direct(player, source, cancelled, progress):
                return _("Playing {title}").format(title=source.title)
        except Cancelled:
            return ""
        except Exception as error:
            _raise_direct_error(source, error)
        raise RuntimeError(NOT_STARTED)
    except Cancelled:
        return ""

