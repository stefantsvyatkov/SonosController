"""Cancellable transfer between standalone rooms (including stereo pairs)."""

from .i18n import _


class Transfer:
    def __init__(self, controller, cancelled):
        self.controller = controller
        self.cancelled = cancelled
        self.source = controller.current().coordinator
        if len(controller.members(self.source.group)) != 1:
            raise RuntimeError(_("Select an ungrouped source."))
        if self.source.get_current_transport_info()["current_transport_state"] != "PLAYING":
            raise RuntimeError(_("Source not playing."))
        self.description = controller.execute("playing")
        self.targets = []
        for group in controller.groups():
            player = group.coordinator
            if player.uid == self.source.uid or len(controller.members(group)) != 1:
                continue
            if player.get_current_transport_info()["current_transport_state"] in ("STOPPED", "PAUSED_PLAYBACK", "NO_MEDIA_PRESENT"):
                self.targets.append(player)
        if not self.targets:
            raise RuntimeError(_("No free rooms."))
        self.index = 0

    def describe(self):
        return _("Transfer: {source} to {target}. {track}. Space or Enter to confirm.").format(
            source=self.source.player_name, target=self.targets[self.index].player_name,
            track=self.description)

    def move(self, delta):
        self.index = (self.index + delta) % len(self.targets)
        return self.targets[self.index].player_name

    def commit(self):
        target = self.targets[self.index]
        if self.cancelled():
            return _("Transfer cancelled.")
        # Recheck eligibility immediately before changing the system.
        if (target.group.coordinator.uid != target.uid
                or len(self.controller.members(target.group)) != 1
                or target.get_current_transport_info()["current_transport_state"] not in
                ("STOPPED", "PAUSED_PLAYBACK", "NO_MEDIA_PRESENT")
                or self.source.group.coordinator.uid != self.source.uid
                or len(self.controller.members(self.source.group)) != 1):
            raise RuntimeError(_("Rooms changed. Restart transfer."))
        if self.cancelled():
            return _("Transfer cancelled.")
        joined = False
        detached = False
        previous_volume = target.volume
        source_volume = self.source.volume
        try:
            target.volume = source_volume
            if self.cancelled():
                target.volume = previous_volume
                return _("Transfer cancelled.")
            joined = True  # A timeout can occur after the speaker applied the request.
            target.join(self.source)
            if self.cancelled():
                target.unjoin()
                joined = False
                target.volume = previous_volume
                return _("Transfer cancelled.")
            self.source.unjoin()
            detached = True
            self.controller.select(target.group)
            return _("Audio transferred to {room}.").format(room=target.player_name)
        except Exception as error:
            if joined and not detached:
                try:
                    # Do not detach the destination if it already became coordinator.
                    if target.group.coordinator.uid == self.source.uid:
                        target.unjoin()
                except Exception:
                    raise RuntimeError(_(
                        "Transfer failed and cleanup could not be confirmed. "
                        "Check the rooms in Sonos.")) from error
            if not detached:
                try:
                    target.volume = previous_volume
                except Exception:
                    raise RuntimeError(_(
                        "Transfer failed. Destination volume could not be restored.")) from error
            raise RuntimeError(_(
                "Transfer could not be confirmed. "
                "Check playback and room grouping in Sonos.")) from error
