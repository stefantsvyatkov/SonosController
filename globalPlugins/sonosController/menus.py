"""Worker-side favorites, sleep timer, and alarm workflows."""

import copy
from datetime import time

from .i18n import _
from .playback import select_and_play, selected_uri
from .util import valid_url
from . import vendors

__all__ = ["valid_url", "favorites", "Favorites", "History", "SleepTimer", "AlarmMenu",
           "parse_alarm", "alarm_mode_supported", "play_favorite", "find_favorite"]


def playable_favorite(item):
    try:
        reference = item.reference
        resources = reference.resources
        kind = reference.item_class.lower()
        return bool(resources and resources[0].uri and (
            kind.startswith("object.item.audioitem") or
            kind.startswith("object.container.album") or
            kind.startswith("object.container.playlistcontainer")))
    except (AttributeError, IndexError, ValueError):
        return False


def favorites(player):
    return [item for item in player.music_library.get_sonos_favorites(complete_result=True)
            if playable_favorite(item)]


def favorite_uri(favorite):
    return favorite.reference.resources[0].uri


def find_favorite(player, uri):
    """The Sonos favorite a history entry points at, if the household still has it."""
    for favorite in favorites(player):
        if favorite_uri(favorite) == uri:
            return favorite
    raise RuntimeError(_("This Sonos favorite is no longer available."))


def title_of(item):
    return item["title"] if isinstance(item, dict) else item.title


class EntryList:
    """A list of the add-on's own entries: Local favorites or the history."""

    #: Which of the library's lists this is.
    list_name = "history"

    def entries(self):
        return self.library.favorites if self.list_name == "favorites" else self.library.history

    def items(self):
        return self.entries()

    def index(self):
        return 0

    def set_index(self, value):
        pass

    def current(self):
        items = self.items()
        if not items:
            return None
        index = min(self.index(), len(items) - 1)
        self.set_index(index)
        return items[index]

    def empty(self):
        return _("History is empty.")

    def describe(self):
        item = self.current()
        if item is None:
            return self.empty()
        return _("{title}, {position} of {total}").format(
            title=title_of(item), position=self.index() + 1, total=len(self.items()))

    def move(self, delta):
        items = self.items()
        if items:
            self.set_index((self.index() + delta) % len(items))
        return self.describe()

    def remove(self, wanted):
        if not self.library.remove(self.list_name, wanted):
            raise RuntimeError(_("This item is no longer in the list."))
        return _("Removed. {entry}").format(entry=self.describe())

    def rename(self, wanted, title):
        if not self.library.rename(self.list_name, wanted, title):
            raise RuntimeError(_("This item is no longer in the list."))
        return _("Renamed. {entry}").format(entry=self.describe())


class Favorites(EntryList):
    """F: the household's Sonos favorites and the add-on's Local favorites.

    Left and Right switch between the two sections; each keeps its own place.
    Only Local favorites can be removed or renamed here: the Sonos favorites
    are shared by everyone in the home and belong to the Sonos app.
    """

    SONOS, LOCAL = 0, 1
    list_name = "favorites"

    def __init__(self, controller, library):
        self.player = controller.current().coordinator
        self.library = library
        library.load()
        self.sonos = favorites(self.player)
        if not self.sonos and not library.favorites:
            raise RuntimeError(_("No favorites."))
        self.section = self.SONOS if self.sonos else self.LOCAL
        self.indexes = [0, 0]

    def items(self):
        return self.sonos if self.section == self.SONOS else self.entries()

    def index(self):
        return self.indexes[self.section]

    def set_index(self, value):
        self.indexes[self.section] = value

    def empty(self):
        if self.section == self.SONOS:
            return _("No Sonos favorites. Add them in the Sonos app.")
        return _("No local favorites. Shift+F adds what is playing.")

    def heading(self):
        if self.current() is None:
            return self.empty()
        heading = _("Sonos favorites. {entry}") if self.section == self.SONOS else _("Local favorites. {entry}")
        return heading.format(entry=self.describe())

    def switch(self):
        self.section = self.LOCAL if self.section == self.SONOS else self.SONOS
        return self.heading()

    def local(self):
        """The selected Local favorite; the Sonos section is not changed from here."""
        if self.section == self.SONOS:
            raise RuntimeError(_("Sonos favorites are changed in the Sonos app."))
        return self.current()


class History(EntryList):
    """H: what the add-on played, newest first."""

    def __init__(self, controller, library):
        self.player = controller.current().coordinator
        self.library = library
        library.load()
        if not library.history:
            raise RuntimeError(_("History is empty."))
        self.position = 0

    def index(self):
        return self.position

    def set_index(self, value):
        self.position = value

    def heading(self):
        return _("History. {entry}").format(entry=self.describe())


def play_favorite(player, favorite, cancelled=lambda: False):
    """Play one Sonos favorite on ``player``'s group and say how it went."""
    if not playable_favorite(favorite):
        raise RuntimeError(_("Favorite has no playable URL."))
    reference = favorite.reference
    uri = reference.resources[0].uri
    kind = str(getattr(reference, "item_class", "")).lower()
    player = player.group.coordinator
    if cancelled():
        return ""
    broadcast = ("broadcast" in kind or uri.startswith(
        ("x-sonosapi-stream:", "x-sonosapi-radio:", "x-rincon-mp3radio:", "http:", "https:")))
    if broadcast:
        def select():
            player.play_uri(uri, meta=favorite.resource_meta_data, start=False)

        def matches():
            return selected_uri(player, uri)
    else:
        # Never retry AddURIToQueue: a timeout could already have appended the item.
        position = player.add_to_queue(reference)

        def select():
            player.play_from_queue(position - 1, start=False)

        def matches():
            return selected_uri(player, uri, position)
    result = select_and_play(player, select, matches, cancelled)
    if result == "starting":
        return _("Starting {title}").format(title=favorite.title)
    if result:
        return _("Playing {title}").format(title=favorite.title)
    return ""


class SleepTimer:
    ROWS = 2

    def __init__(self, controller):
        self.player = controller.current().coordinator
        remaining = self.player.get_sleep_timer()
        self.custom = max(1, min(90, round((remaining or 300) / 60)))
        self.row = 0
        self.index = 0
        self.values = [0] + list(range(5, 91, 5))

    def describe(self):
        if self.row:
            return _("Custom, {minutes} minutes.").format(minutes=self.custom)
        value = self.values[self.index]
        return _("Timer off") if value == 0 else _("{minutes} minutes").format(minutes=value)

    def move(self, delta):
        # Wrap like every other list in the add-on rather than stopping silently.
        self.row = (self.row + delta) % self.ROWS
        return self.describe()

    def adjust(self, delta):
        if self.row:
            self.custom = max(1, min(90, self.custom + delta))
            return _("{minutes} minutes").format(minutes=self.custom)
        self.index = max(0, min(len(self.values) - 1, self.index + delta))
        return self.describe()

    def apply(self):
        value = self.custom if self.row else self.values[self.index]
        self.player.group.coordinator.set_sleep_timer(value * 60 if value else None)
        if not value:
            return _("Timer off")
        return _("Timer, {minutes} minutes").format(minutes=value)


def alarm_mode_supported(context, source):
    """Only identified collections have a meaningful alarm playback order."""
    kind = ""
    if source >= 3:
        kind = getattr(context["favorites"][source - 3].reference, "item_class", "")
    elif source == 0 and context.get("alarm"):
        from xml.etree import ElementTree as ET
        try:
            root = ET.fromstring(context["alarm"].program_metadata or "")
            kind = root.findtext(".//{urn:schemas-upnp-org:metadata-1-0/upnp/}class", "")
        except (ET.ParseError, AttributeError):
            pass
    return kind.lower().startswith(("object.container.album", "object.container.playlistcontainer"))


def parse_alarm(data):
    def clock(text, optional=False):
        if optional and not text.strip():
            return None
        try:
            parts = text.strip().split(":")
            if len(parts) in (2, 3) and all(p.isdigit() for p in parts):
                return time(*map(int, parts))
        except ValueError:
            pass
        raise ValueError(_("Use HH:MM or HH:MM:SS."))

    is_valid_recurrence = vendors.soco_submodule("alarms").is_valid_recurrence
    recurrence = data["recurrence"].strip().upper()
    if not is_valid_recurrence(recurrence):
        raise ValueError(_("Use DAILY, ONCE, WEEKDAYS, WEEKENDS, or ON_0123456 (0 is Sunday)."))
    duration = clock(data["duration"], True)
    if duration is not None and duration == time(0, 0, 0):
        raise ValueError(_("A custom duration must be longer than zero."))
    return dict(start_time=clock(data["time"]), duration=duration,
                recurrence=recurrence, enabled=data["enabled"], volume=int(data["volume"]),
                include_linked_zones=data["linked"], play_mode=data["play_mode"])


class AlarmMenu:
    def __init__(self, controller):
        Alarms = vendors.soco_submodule("alarms").Alarms
        self.controller = controller
        self.player = controller.current().coordinator
        self.store = Alarms()
        self.index = 0
        self.reload()

    def reload(self):
        try:
            self._update()
        except Exception:
            # The registry is a process-wide singleton, so a stale alarm-list
            # version from an earlier network survives a failure and would keep
            # rejecting the list.  Forget it once and read again.  The public
            # setter parses the value it is given, so clear the fields directly.
            self.store._last_alarm_list_version = None
            self.store.last_uid = None
            self.store.last_id = 0
            self._update()
        self.items = sorted(list(self.store), key=lambda a: (a.start_time, str(a.alarm_id)))
        self.index = min(self.index, len(self.items))

    def _update(self):
        self.store.update(self.player)
        for room in self.controller.rooms:
            self.store.update_skipped(room)

    def current(self):
        return self.items[self.index] if self.index < len(self.items) else None

    def describe(self):
        alarm = self.current()
        if alarm is None:
            return _("New alarm")
        room = alarm.zone.player_name if alarm.zone else _("Room unavailable")
        state = _("on") if alarm.enabled else _("off")
        return f"{alarm.start_time:%H:%M}, {room}, {state}, {alarm.recurrence}"

    def move(self, delta):
        self.index = (self.index + delta) % (len(self.items) + 1)
        return self.describe()

    def toggle(self):
        alarm = self.current()
        if alarm is None:
            return _("Enter to create.")
        old = alarm.enabled
        try:
            alarm.enabled = not old
            alarm.save()
        except Exception:
            alarm.enabled = old
            raise
        return self.describe()

    def editor(self, new=False):
        alarm = None if new else self.current()
        rooms = list(self.controller.rooms)
        if alarm is not None and alarm.zone is None:
            raise RuntimeError(_("Alarm room unavailable."))
        try:
            choices = favorites(self.player)
        except Exception as error:
            raise RuntimeError(_("Could not load alarm sounds. Try again.")) from error
        return {"alarm": alarm, "rooms": rooms, "roomNames": [p.player_name for p in rooms],
                "roomIds": [p.uid for p in rooms],
                "favorites": choices, "favoriteNames": [f.title for f in choices],
                "defaultRoom": self.player.uid}

    def save(self, payload):
        Alarm = vendors.soco_submodule("alarms").Alarm
        data, context = payload
        values = parse_alarm(data)
        if not alarm_mode_supported(context, data["source"]):
            values["play_mode"] = "NORMAL"
        room = context["rooms"][data["room"]]
        old = context["alarm"]
        # Work on a copy so a failed save cannot leave the registry holding
        # half-applied values.
        alarm = copy.copy(old) if old is not None else Alarm(room)
        alarm.zone = room
        alarm.room_uuid = room.uid
        source = data["source"]
        if source == 1:
            values.update(program_uri=None, program_metadata="")
        elif source == 2:
            if not valid_url(data["url"]):
                raise ValueError(_("Enter an HTTP or HTTPS audio URL."))
            values.update(program_uri=data["url"].strip(), program_metadata="")
        elif source >= 3:
            favorite = context["favorites"][source - 3]
            values.update(program_uri=favorite.reference.resources[0].uri,
                          program_metadata=favorite.resource_meta_data)
        elif old is None:
            values.update(program_uri=None, program_metadata="")
        alarm.update(**values)
        alarm.save()
        self.reload()
        return _("Alarm saved.")

    def delete(self, alarm_id):
        # Resolve by ID, not the potentially changed list selection.
        alarm = next((a for a in self.items if a.alarm_id == alarm_id), None)
        if alarm is None:
            raise RuntimeError(_("Alarm is no longer available."))
        alarm.remove()
        self.reload()
        return _("Alarm deleted. {entry}").format(entry=self.describe())
