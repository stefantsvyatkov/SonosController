"""Experimental FFmpeg fallback layered over the public URL player."""

from .i18n import _
from .relay import fallback_modes
from .urls import (
    AudioSource,
    Cancelled,
    _confirmed_start,
    _direct,
    _raise_direct_error,
    metadata,
    resolve_audio,
)
from .util import Played


#: Speaker responses that mean "wrong format", which local conversion can fix.
FORMAT_REFUSALS = (
    _("The speaker rejected the audio format."),
    _("The speaker rejected the stream."),
    _("The speaker did not start playing the stream."),
    _("Direct Sonos URL playback does not support Opus."),
)


def fallback_allowed(error):
    code = str(getattr(error, "error_code", ""))
    return code in ("703", "704", "714", "716", "737", "738", "739") or str(error) in FORMAT_REFUSALS


def play_url(player, url, cancelled=lambda: False, progress=lambda message: None,
             relay=None, relay_clients=()):
    """Try Sonos directly, then the isolated remux and transcode stages."""
    try:
        source = resolve_audio(url, cancelled=cancelled)
        if cancelled():
            return ""
        player = player.group.coordinator
        if relay is not None:
            relay.stop()
        direct_error = None
        if source.mime == "audio/opus":
            direct_error = RuntimeError(_("Direct Sonos URL playback does not support Opus."))
        else:
            try:
                if _direct(player, source, cancelled, progress):
                    return Played(_("Playing {title}").format(title=source.title), source.title)
            except Cancelled:
                return ""
            except Exception as error:
                direct_error = error
        if relay is None or not relay.available or not fallback_allowed(direct_error):
            _raise_direct_error(source, direct_error)
        progress(_("Converting locally."))
        last_error = direct_error
        for mode in fallback_modes(source):
            session = None
            try:
                if cancelled():
                    raise Cancelled()
                session = relay.start(player, source, mode, relay_clients)
                local_source = AudioSource(session.url, "radio", session.content_type, source.title)
                _confirmed_start(
                    player,
                    session.url,
                    metadata(local_source, session.url),
                    cancelled,
                    progress,
                    session.failed.is_set,
                )
                return Played(_("Playing {title}").format(title=source.title), source.title)
            except Cancelled:
                if session is not None:
                    relay.stop(session)
                return ""
            except Exception as error:
                last_error = error
                if session is not None:
                    requested = session.requested.is_set()
                    relay.stop(session)
                    if not requested:
                        raise RuntimeError(_(
                            "Local conversion could not be reached. Check firewall access for NVDA.")) from error
        raise RuntimeError(_("Local conversion could not start.")) from last_error
    except Cancelled:
        return ""
