"""The one place the optional FFmpeg relay plugs into the layer.

The public add-on ships this file unchanged: it plays a URL directly and every
lifecycle call does nothing.  The experimental personal build replaces only
this module, so the rest of the add-on never has to know the relay exists and
a refactor here cannot silently break that build.

See ``experimental/ffmpeg-relay`` for the variant that transcodes formats a
speaker cannot decode.  It is not part of the public package because it needs
an FFmpeg executable.
"""

from .urls import play_url as _play_url


class Relay:
    """No-op stand-in for the transcoding relay."""

    #: The experimental build sets this so the UI can mention the fallback.
    available = False

    def play_url(self, player, url, cancelled, progress, addresses=()):
        """Play ``url`` on ``player``.  The public build streams it directly."""
        return _play_url(player, url, cancelled, progress)

    def stop(self):
        """Release any stream started for a previous source."""

    def close(self):
        """Shut the relay down while NVDA unloads the add-on."""
