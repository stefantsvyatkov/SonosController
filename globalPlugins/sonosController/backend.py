"""Local Sonos operations, called exclusively from the worker thread."""

from .i18n import _
from .media import describe, seconds
from .util import PAGE_STEP, timestamp, transient

#: How much each volume key changes the group volume.
VOLUME_STEPS = {"up": 1, "down": -1, "pageUp": PAGE_STEP, "pageDown": -PAGE_STEP}

#: Sonos returns these when a transport command does not apply right now.
REJECTED_CODES = ("701", "710", "711")


class Controller:
    def __init__(self, discover, selected="", remember=lambda uid: None):
        self.discover = discover
        self.selected = selected
        self.remember = remember
        self.rooms = []

    def refresh(self):
        self.rooms = sorted(self.discover(timeout=4) or [],
                            key=lambda p: (p.player_name.casefold(), p.uid))
        if not self.rooms:
            raise RuntimeError(_("No Sonos rooms found."))

    def groups(self):
        if not self.rooms:
            self.refresh()
        groups = {}
        failure = None
        # The topology is shared, so the first room that answers serves the
        # rest.  Ask the selected room first: a portable that has gone to sleep
        # must not hold up every command, or stop the others being listed.
        for room in sorted(self.rooms, key=lambda p: p.uid != self.selected):
            try:
                group = room.group
            except Exception as error:
                if not transient(error):
                    raise
                failure = error
                continue
            # Topology can be mid-update, in which case SoCo has no group yet.
            if group is None or group.coordinator is None:
                continue
            groups[group.coordinator.uid] = group
        if not groups:
            if failure is not None:
                raise failure
            raise RuntimeError(_("Room grouping is still updating. Try again."))
        return sorted(groups.values(), key=lambda g: self.label(g).casefold())

    def members(self, group):
        return sorted((p for p in group.members if p in self.rooms and p.is_visible),
                      key=lambda p: p.player_name.casefold())

    def label(self, group):
        return ", ".join(p.player_name for p in self.members(group)) or group.coordinator.player_name

    def current(self):
        groups = self.groups()
        for group in groups:
            if any(p.uid == self.selected for p in group.members):
                return group
        group = groups[0]
        self.select(group)
        return group

    def select(self, group):
        self.selected = group.coordinator.uid
        self.remember(self.selected)

    def describe(self, group):
        parts = []
        for p in self.members(group):
            try:
                status = _("{room}, {volume} percent, muted") if p.mute else _("{room}, {volume} percent")
                parts.append(status.format(room=p.player_name, volume=p.volume))
            except Exception as error:
                # A grouped portable that went to sleep still leaves the others to hear.
                if not transient(error):
                    raise
                parts.append(_("{room}, not responding").format(room=p.player_name))
        return "; ".join(parts) or self.label(group)

    def execute(self, action):
        if action == "open":
            self.refresh()
            return _("Sonos, {status}").format(status=self.describe(self.current()))
        if action in ("left", "right"):
            groups = self.groups()
            current = self.current()
            index = next((i for i, g in enumerate(groups)
                          if g.coordinator.uid == current.coordinator.uid), None)
            if index is None:
                raise RuntimeError(_("Room grouping has changed. Try again."))
            group = groups[(index + (1 if action == "right" else -1)) % len(groups)]
            self.select(group)
            return self.describe(group)
        group = self.current()
        player = group.coordinator
        if action in ("elapsed", "remaining"):
            track = player.get_current_track_info()
            position, duration = seconds(track.get("position")), seconds(track.get("duration"))
            if position is None or not duration:
                return _("Track time unavailable.")
            value = position if action == "elapsed" else max(0, duration - position)
            label = _("Elapsed") if action == "elapsed" else _("Remaining")
            return f"{label} {timestamp(value)}"
        if action == "copyTrack":
            track = player.get_current_track_info()
            uri = track.get("uri", "").lower()
            title = str(track.get("title") or "").strip()
            if (uri.startswith(("x-sonos-htastream:", "x-rincon-stream:"))
                    or title in ("", "NOT_IMPLEMENTED")):
                return _("Track information cannot be copied.")
            if (uri.startswith(("x-sonosapi-stream:", "x-sonosapi-radio:", "x-rincon-mp3radio:"))
                    and not track.get("artist")):
                return _("Track information cannot be copied.")
            text = " — ".join(str(track.get(k) or "").strip()
                              for k in ("title", "artist") if track.get(k))
            return {"copy": text}
        if action in ("seekBack", "seekForward"):
            track = player.get_current_track_info()
            position, duration = seconds(track.get("position")), seconds(track.get("duration"))
            if (not {"Seek", "X_DLNA_SeekTime"}.intersection(self.transport_actions(player))
                    or position is None or not duration):
                return _("Seeking unavailable.")
            target = max(0, min(duration - 1, position + (10 if action == "seekForward" else -10)))
            stamp = timestamp(target)
            try:
                player.seek(position=stamp)
            except Exception as error:
                if str(getattr(error, "error_code", "")) in REJECTED_CODES:
                    return _("Seeking unavailable.")
                raise
            return _("Position {time}").format(time=stamp)
        if action == "battery":
            reports = []
            unread = False
            for room in self.members(group):
                try:
                    info = room.get_battery_info(timeout=3)
                except Exception as error:
                    name = type(error).__name__
                    # One silent speaker must not hide the others' batteries.
                    if isinstance(error, TimeoutError) or "Timeout" in name:
                        unread = True
                        continue
                    # An older speaker answers the battery page with an error
                    # status: it has no battery to report.
                    if (name == "NotSupportedException" or isinstance(error, ConnectionError)
                            or "ConnectionError" in name):
                        continue
                    raise
                level = info.get("Level")
                if level is not None:
                    power = str(info.get("PowerSource", "")).replace("_", " ").lower()
                    report = (_("{room}, battery {level} percent, power source {source}") if power
                              else _("{room}, battery {level} percent"))
                    reports.append(report.format(room=room.player_name, level=level, source=power))
            if reports:
                return "; ".join(reports)
            return _("Could not read battery level.") if unread else _("Battery information unavailable.")
        if action in VOLUME_STEPS or action == "mute":
            try:
                if action == "mute":
                    group.mute = not group.mute
                    return _("Muted") if group.mute else _("Unmuted")
                volume = group.set_relative_volume(VOLUME_STEPS[action])
                return _("{volume} percent").format(volume=volume)
            except Exception as error:
                # A Port or Connect set to fixed line output refuses volume
                # changes; say why instead of quoting a Sonos error number.
                fixed = self.fixed_rooms(group) if getattr(error, "error_code", None) else []
                if fixed:
                    return _("Fixed volume on {rooms}.").format(rooms=", ".join(fixed))
                raise
        if action == "playPause":
            actions = self.transport_actions(player)
            if player.get_current_transport_info()["current_transport_state"] == "PLAYING":
                if "Pause" not in actions:
                    if "Stop" not in actions:
                        return _("Pause and stop unavailable.")
                    player.stop()
                    return _("Stopped")
                player.pause()
                return _("Pause")
            if "Play" not in actions:
                return _("Nothing to play. Select music in the Sonos app.")
            player.play()
            return _("Play")
        if action in ("previous", "next"):
            if action.capitalize() not in self.transport_actions(player):
                return _("Track skipping unavailable.")
            try:
                getattr(player, action)()
            except Exception as error:
                if str(getattr(error, "error_code", "")) in ("701", "711"):
                    return _("Track skipping unavailable.")
                raise
            return _("Previous track") if action == "previous" else _("Next track")
        if action == "playing":
            track = player.get_current_track_info()
            if not track.get("title"):
                try:
                    extra = player.avTransport.GetMediaInfo([("InstanceID", 0)])
                    track = dict(track, **extra)
                except Exception:
                    pass
            state = player.get_current_transport_info()["current_transport_state"]
            return describe(track, state.replace("_", " ").lower())
        raise ValueError(_("This Sonos command is unavailable."))

    def fixed_rooms(self, group):
        names = []
        for room in self.members(group):
            try:
                if room.fixed_volume:
                    names.append(room.player_name)
            except Exception:
                continue
        return names

    @staticmethod
    def transport_actions(player):
        response = player.avTransport.GetCurrentTransportActions([("InstanceID", 0)])
        return {action.strip() for action in response.get("Actions", "").split(",") if action.strip()}
