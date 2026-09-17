"""Select the rooms that should retain the source's current playback."""

import time

from .i18n import _

# Spoken whenever a cancellation lands between two speaker requests.
PARTIAL = _("Cancelled. Some changes may remain.")


class Grouping:
    def __init__(self, controller, cancelled):
        self.controller = controller
        self.cancelled = cancelled
        self.source = controller.current().coordinator
        if self.source.get_current_transport_info()["current_transport_state"] != "PLAYING":
            raise RuntimeError(_("Select a playing room."))
        self.rooms = list(controller.rooms)
        self.refresh(self.source)
        self.original = self.member_ids(self.source.group)
        if not self.original.issubset({p.uid for p in self.rooms}):
            raise RuntimeError(_("Group members unavailable."))
        self.selected = set(self.original)
        self.index = next((i for i, p in enumerate(self.rooms) if p.uid == self.source.uid), 0)

    @staticmethod
    def member_ids(group):
        return {p.uid for p in group.members if p.is_visible}

    @staticmethod
    def refresh(player):
        state = getattr(player, "zone_group_state", None)
        if state is not None:
            state.clear_cache()
            state.poll(player)

    def wait_for(self, player, condition):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if self.cancelled():
                return False
            try:
                self.refresh(player)
                if condition():
                    return True
            except Exception as error:
                if not (isinstance(error, (TimeoutError, ConnectionError))
                        or "Timeout" in type(error).__name__ or "ConnectionError" in type(error).__name__):
                    raise
            time.sleep(0.2)
        raise RuntimeError(_("Group update not confirmed."))

    def change(self, operation, player, condition):
        try:
            operation()
        except Exception as error:
            if not (isinstance(error, (TimeoutError, ConnectionError))
                    or "Timeout" in type(error).__name__ or "ConnectionError" in type(error).__name__):
                raise
        # Never repeat a mutation after an ambiguous timeout; verify its effect.
        return self.wait_for(player, condition)

    def describe(self):
        room = self.rooms[self.index]
        state = _("selected") if room.uid in self.selected else _("not selected")
        return f"{room.player_name}, {state}"

    def move(self, delta):
        self.index = (self.index + delta) % len(self.rooms)
        return self.describe()

    def toggle(self):
        uid = self.rooms[self.index].uid
        if uid in self.selected:
            self.selected.remove(uid)
        else:
            self.selected.add(uid)
        return self.describe()

    def commit(self):
        if not self.selected:
            raise RuntimeError(_("Select at least one room. Start group selection again."))
        if self.cancelled():
            return _("Grouping cancelled.")
        self.refresh(self.source)
        if self.member_ids(self.source.group) != self.original or self.source.group.coordinator.uid != self.source.uid:
            raise RuntimeError(_("Group changed. Reopen G."))
        changed = False
        try:
            # Add destinations before removing any source, keeping playback alive.
            for room in self.rooms:
                if room.uid in self.selected - self.original:
                    if self.cancelled():
                        return PARTIAL
                    changed = True
                    if not self.change(lambda: room.join(self.source), self.source,
                                       lambda: room.uid in self.member_ids(self.source.group)):
                        return PARTIAL
            removed = [p for p in self.rooms if p.uid in self.original - self.selected]
            # The coordinator leaves last so the retained rooms inherit playback.
            removed.sort(key=lambda p: p.uid == self.source.uid)
            for room in removed:
                if self.cancelled():
                    return PARTIAL
                changed = True
                retained = next(p for p in self.rooms if p.uid in self.selected)
                if not self.change(room.unjoin, retained,
                                   lambda: room.uid not in self.member_ids(retained.group)):
                    return PARTIAL
            target = next(p for p in self.rooms if p.uid in self.selected)
            if not self.wait_for(target, lambda: self.member_ids(target.group) == self.selected):
                return PARTIAL
            self.controller.select(target.group)
            return _("Playing rooms: ") + ", ".join(
                p.player_name for p in self.rooms if p.uid in self.selected)
        except Exception as error:
            if changed:
                raise RuntimeError(_("Grouping failed. Some changes may remain. Check G.")) from error
            raise
