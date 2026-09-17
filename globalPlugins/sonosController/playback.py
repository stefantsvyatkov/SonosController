"""Confirm asynchronous Sonos playback without re-enqueuing content."""

import time
from urllib.parse import unquote, urlsplit, parse_qsl

from .i18n import _
from .util import transient

__all__ = ["transient", "source_identity", "selected_uri", "select_and_play"]


def source_identity(uri):
    parts = urlsplit(uri)
    if parts.scheme in ("x-sonosapi-stream", "x-sonosapi-radio"):
        # Sonos rewrites playback flags when selecting a favorite; the service,
        # account and content identifier must still match.
        query = tuple(sorted((key, value) for key, value in parse_qsl(parts.query) if key != "flags"))
        return parts.scheme, unquote(parts.path), query
    return unquote(uri)


def selected_uri(player, uri, queue_position=None):
    current = player.get_current_media_info().get("uri", "")
    if queue_position is None:
        return source_identity(current) == source_identity(uri)
    if not current.startswith("x-rincon-queue:"):
        return False
    return str(player.get_current_track_info().get("playlist_position")) == str(queue_position)


def select_and_play(player, select, matches, cancelled=lambda: False):
    if cancelled():
        return False
    try:
        select()
    except Exception as error:
        if not transient(error):
            raise
        # An expired response is ambiguous: only continue if this exact source was selected.
        if cancelled() or not matches():
            raise RuntimeError(_("Source selection could not be confirmed.")) from error
    if cancelled():
        return False
    pending_error = None
    try:
        player.play()
    except Exception as error:
        if not transient(error):
            raise
        pending_error = error
    last_state = None
    for attempt in range(5):
        if cancelled():
            return False
        try:
            state = player.get_current_transport_info()
            if matches():
                last_state = state.get("current_transport_state")
                if state.get("current_transport_state") == "PLAYING":
                    return True
                if (attempt < 2 and pending_error is not None
                        and str(getattr(pending_error, "error_code", "")) == "701"
                        and state.get("current_transport_state") in ("STOPPED", "PAUSED_PLAYBACK")):
                    time.sleep(0.3)
                    if cancelled():
                        return False
                    try:
                        player.play()
                        pending_error = None
                    except Exception as error:
                        if not transient(error):
                            raise
                        pending_error = error
            else:
                raise RuntimeError(_("Playback source changed."))
        except Exception as error:
            if not transient(error):
                raise
            pending_error = error
        if attempt < 4:
            time.sleep(0.3)
    if last_state == "TRANSITIONING":
        return "starting"
    raise RuntimeError(_("Playback not confirmed. Press P to check.")) from pending_error
