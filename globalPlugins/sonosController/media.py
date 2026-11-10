"""Conservative source identification and metadata fallbacks."""

import xml.etree.ElementTree as ET
from urllib.parse import unquote

from .i18n import _
from .util import readable


def seconds(value):
    try:
        parts = str(value).split(":")
        if len(parts) != 3:
            return None
        hours, minutes, secs = map(int, parts)
        if hours < 0 or not 0 <= minutes < 60 or not 0 <= secs < 60:
            return None
        return hours * 3600 + minutes * 60 + secs
    except (ValueError, TypeError):
        return None


#: A station's source URI, and the stream URI a speaker reports while it plays one.
RADIO = ("x-rincon-mp3radio:", "x-sonosapi-stream:", "x-sonosapi-radio:", "x-sonosapi-hls:",
         "aac:", "hls-radio:")


def source_name(uri):
    uri = (uri or "").lower()
    if "spotify" in uri:
        return "Spotify"
    if "bluetooth" in uri:
        return _("Bluetooth")
    if "airplay" in uri:
        return "AirPlay"
    for prefixes, name in (
        (RADIO, _("Internet radio")),
        (("x-sonos-htastream:",), _("TV")),
        (("x-rincon-stream:",), _("Line-in")),
        (("x-sonos-airplay:",), "AirPlay"),
        (("x-sonos-vli:",), _("External audio session")),
        (("x-file-cifs:",), _("Music library")),
        (("x-rincon-queue:",), _("Sonos queue")),
        (("x-rincon:",), _("Grouped playback")),
        (("http:", "https:", "hls-static:"), _("Network audio")),
    ):
        if uri.startswith(prefixes):
            return name
    return _("Audio source") if uri else ""


def fields(xml):
    """The text of each DIDL element, by its name without the namespace."""
    values = {}
    try:
        for node in ET.fromstring(xml or "").iter():
            key = node.tag.rsplit("}", 1)[-1]
            if node.text and node.text.strip() not in ("NOT_IMPLEMENTED", ""):
                values.setdefault(key, node.text.strip())
    except ET.ParseError:
        pass
    return values


def spoken(text):
    """Track text fit to be said, or "" for none.

    A speaker reports a station's name in the station's own encoding read as
    Latin-1, and its own placeholders while it connects ("ZPSTR_BUFFERING").
    """
    text = " ".join(readable(str(text or "")).split())
    return "" if text.startswith("ZPSTR_") or text == "NOT_IMPLEMENTED" else text


def describe(track, state, media=None):
    """What plays, for P.  ``media`` is GetMediaInfo: the source the speaker was given."""
    media = media or {}
    values = fields(track.get("metadata", ""))
    chosen = fields(media.get("CurrentURIMetaData", ""))
    uri = track.get("uri") or media.get("CurrentURI") or ""
    source_uri = media.get("CurrentURI") or uri
    radio = source_uri.lower().startswith(RADIO) or uri.lower().startswith(RADIO)
    # A station's name is in what was chosen; the track holds what it plays now.
    station = chosen.get("title", "") if radio else ""
    if not radio:
        for key, value in chosen.items():
            values.setdefault(key, value)
    title = track.get("title") or values.get("title", "")
    artist = track.get("artist") or values.get("creator", "")
    stream = values.get("streamContent", "")
    station, title, artist, stream = map(spoken, (station, title, artist, stream))
    # A speaker with no tags to read names what it plays after the end of its
    # address ("thevoicefm.m3u8"), which says nothing a listener wants: a
    # station is named by its source, anything else by what was chosen.
    if title and unquote(uri).endswith(title):
        title = "" if radio else chosen.get("title", title)
    # "Artist - Song" again after the song and the artist.
    if stream and title and artist and title in stream and artist in stream:
        stream = ""
    details = list(dict.fromkeys(v for v in (station, title, artist, stream) if v))
    source = source_name(source_uri if radio else uri)
    if source:
        details.insert(0, source)
    # TV and line-in sound has no track to describe, so saying so is noise.
    live_input = uri.lower().startswith(("x-sonos-htastream:", "x-rincon-stream:"))
    if not station and not title and not artist and not stream and not live_input:
        details.append(_("Track information unavailable"))
    normalized = state.replace("_", " ").lower()
    details.append({"paused playback": "paused", "no media present": "no media"}.get(normalized, normalized))
    return ", ".join(details)
