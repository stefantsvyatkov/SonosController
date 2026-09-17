"""Verified playback modes for the selected Sonos queue."""

import time

from .i18n import _


class PlaybackSettings:
    def __init__(self, controller):
        self.player = controller.current().coordinator
        self.check_source()
        self.options = [("shuffle", _("Shuffle"), 1), ("repeat", _("Repeat"), 2)]
        try:
            if isinstance(self.player.cross_fade, bool):
                self.options.append(("cross_fade", _("Crossfade"), 1))
        except Exception:
            pass
        self.index = 0
        self.unavailable = []

    def check_source(self):
        uri = self.player.get_current_media_info().get("uri", "")
        if not uri.startswith("x-rincon-queue:"):
            raise RuntimeError(_("Playback settings require a Sonos queue."))

    def read(self):
        value = getattr(self.player, self.options[self.index][0])
        return 2 if value == "ONE" else int(bool(value))

    def describe(self):
        self.check_source()
        key, label, maximum = self.options[self.index]
        value = self.read()
        names = (_("off"), _("all"), _("one")) if key == "repeat" else (_("off"), _("on"))
        text = names[value]
        return f"{label}, {text}"

    def move(self, delta):
        self.index = (self.index + delta) % len(self.options)
        return self.describe()

    def adjust(self, delta):
        self.check_source()
        key, label, maximum = self.options[self.index]
        old = self.read()
        value = max(0, min(maximum, old + delta))
        if value != old:
            setattr(self.player, key, (False, True, "ONE")[value] if key == "repeat" else bool(value))
            for delay in (0, 0.2, 0.4):
                if delay:
                    time.sleep(delay)
                if self.read() == value:
                    break
            else:
                raise RuntimeError(_("{label} was not applied.").format(label=label))
        return self.describe()
