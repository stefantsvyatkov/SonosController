"""The one place the optional FFmpeg fallback plugs into the layer.

URL playback always tries the speaker first.  Only when Sonos refuses the
format does this look for an FFmpeg and, if one is there, remux or transcode
the stream locally so the speaker can play it.

Nothing is bundled, and nothing is fetched behind the user's back.  When the
speaker refuses a stream that conversion would fix and no FFmpeg is installed,
:class:`NeedsFFmpeg` asks the layer to offer the download; declining leaves
playback exactly as it was, with the speaker's refusal reported.
"""

import threading

from . import ffmpeg as ffmpeg_finder
from .i18n import _
from .urls import play_url as _play_url

class NeedsFFmpeg(Exception):
    """The speaker refused a stream that conversion would have played.

    Carries the speaker's own complaint, so whoever offers the download can
    say what went wrong as well as what would fix it.
    """

    def __init__(self, problem):
        super().__init__(str(problem))
        self.problem = problem


class Relay:
    """Runs the local conversion when the machine has a usable FFmpeg."""

    def __init__(self, configured="", finder=None):
        self._configured = configured
        self._finder = finder or ffmpeg_finder.find
        self._lock = threading.Lock()
        self._manager = None
        self._checked = False
        self._reason = ""
        self.executable = None
        self.version = ""

    # -- discovery -------------------------------------------------------
    @property
    def available(self):
        """Whether a usable FFmpeg was found.  Probed once, then remembered."""
        self._resolve()
        return self.executable is not None

    @property
    def reason(self):
        """Why the fallback is unavailable, for the user."""
        self._resolve()
        return self._reason

    def _resolve(self):
        with self._lock:
            if self._checked:
                return
            self._checked = True
            path, detail, _encoders = self._finder(self._configured)
            if path is None:
                self._reason = detail
            else:
                self.executable = path
                self.version = detail

    def recheck(self):
        """Forget the cached result, for when the user installs FFmpeg."""
        with self._lock:
            self._checked = False
            self.executable = None
            self.version = ""
            self._reason = ""

    def _relay(self):
        """Build the relay manager lazily; it starts no process until used."""
        with self._lock:
            if self._manager is None:
                from .relay import RelayManager
                self._manager = RelayManager(executable=self.executable)
            return self._manager

    # -- the layer's interface -------------------------------------------
    def play_url(self, player, url, cancelled, progress, addresses=()):
        if self.available:
            from .relay_urls import play_url as relay_play_url
            return relay_play_url(player, url, cancelled, progress, self._relay(), addresses)
        try:
            return _play_url(player, url, cancelled, progress)
        except Exception as error:
            # A format the speaker refuses is exactly what conversion fixes, so
            # say that it could have, rather than leaving the user guessing.
            from .relay_urls import fallback_allowed
            if fallback_allowed(error):
                raise NeedsFFmpeg(error) from error
            raise

    def describe(self):
        """A short line about the fallback, for the status command."""
        if self.available:
            return _("Local conversion available.")
        return self.reason

    def stop(self):
        with self._lock:
            manager = self._manager
        if manager is not None:
            manager.stop()

    def close(self):
        with self._lock:
            manager, self._manager = self._manager, None
        if manager is not None:
            manager.close()
