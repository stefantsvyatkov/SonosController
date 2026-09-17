"""Conservative source identification and metadata fallbacks."""

import xml.etree.ElementTree as ET

from .i18n import _


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


def source_name(uri):
    uri = (uri or "").lower()
    if "spotify" in uri:
        return "Spotify"
    if "bluetooth" in uri:
        return _("Bluetooth")
    if "airplay" in uri:
        return "AirPlay"
    for prefixes, name in (
        (("x-rincon-mp3radio:", "x-sonosapi-stream:", "x-sonosapi-radio:"), _("Internet radio")),
        (("x-sonos-htastream:",), _("TV")),
        (("x-rincon-stream:",), _("Line-in")),
        (("x-sonos-airplay:",), "AirPlay"),
        (("x-sonos-vli:",), _("External playback session")),
        (("x-file-cifs:",), _("Music library")),
        (("x-rincon-queue:",), _("Sonos queue")),
        (("x-rincon:",), _("Grouped playback")),
        (("http:", "https:"), _("Network audio")),
    ):
        if uri.startswith(prefixes):
            return name
    return _("Audio source") if uri else ""


def describe(track, state):
    values = {}
    for field in ("metadata", "CurrentURIMetaData"):
        try:
            for node in ET.fromstring(track.get(field, "")).iter():
                key = node.tag.rsplit("}", 1)[-1]
                if node.text and node.text.strip() not in ("NOT_IMPLEMENTED", ""):
                    values.setdefault(key, node.text.strip())
        except ET.ParseError:
            pass
    title = track.get("title") or values.get("title", "")
    artist = track.get("artist") or values.get("creator", "")
    stream = values.get("streamContent", "")
    details = [v for v in (title, artist, stream) if v and v != "NOT_IMPLEMENTED"]
    details = list(dict.fromkeys(details))
    source = source_name(track.get("uri") or track.get("CurrentURI"))
    if source:
        details.insert(0, source)
    if not title and not artist and not stream:
        details.append(_("Track details unavailable"))
    normalized = state.replace("_", " ").lower()
    details.append({"paused playback": "paused", "no media present": "no media"}.get(normalized, normalized))
    return ", ".join(details)
