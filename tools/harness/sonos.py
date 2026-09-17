"""A simulated Sonos household that behaves like the SoCo objects the add-on uses.

Every operation that would cross the network goes through ``Household.call``,
so a test can count round trips and give them a realistic latency.  That makes
the emulator useful for responsiveness questions, not just for logic.
"""

import datetime
import itertools
import threading
import time
import types


# Attributes the add-on reads or writes as speaker settings.  Each access is a
# separate UPnP request on real hardware, so each one is counted here.
SETTING_KEYS = {
    "volume", "mute", "bass", "treble", "loudness", "status_light",
    "buttons_enabled", "balance", "trueplay", "night_mode", "dialog_mode",
    "speech_enhance_enabled", "audio_delay", "surround_enabled",
    "surround_full_volume_enabled", "surround_volume_tv", "surround_volume_music",
    "sub_enabled", "sub_gain", "sub_crossover", "fixed_volume",
    "cross_fade", "shuffle", "repeat",
}

MODELS = {
    "Sonos Era 100": {
        "settings": ["volume", "mute", "bass", "treble", "loudness", "status_light",
                     "buttons_enabled", "balance", "trueplay"],
        "flags": {},
    },
    "Sonos Arc": {
        "settings": ["volume", "mute", "bass", "treble", "loudness", "status_light",
                     "buttons_enabled", "night_mode", "dialog_mode", "audio_delay",
                     "surround_enabled", "surround_full_volume_enabled",
                     "surround_volume_tv", "surround_volume_music",
                     "sub_enabled", "sub_gain"],
        "flags": {"is_soundbar": True, "has_satellites": True, "has_subwoofer": True,
                  "_ht_sat_chan_map": "LF,RF;RINCON_1:LR;RINCON_2:RR"},
    },
    "Sonos Move 2": {
        "settings": ["volume", "mute", "bass", "treble", "loudness", "status_light",
                     "buttons_enabled", "balance"],
        "flags": {"battery": True},
    },
    "Sonos Amp": {
        "settings": ["volume", "mute", "bass", "treble", "loudness", "status_light",
                     "buttons_enabled", "balance", "sub_enabled", "sub_gain", "sub_crossover"],
        "flags": {"has_subwoofer": True},
    },
    "Sonos Port": {
        "settings": ["volume", "mute", "bass", "treble", "loudness", "status_light",
                     "buttons_enabled", "balance", "fixed_volume"],
        "flags": {"supports_fixed_volume": True},
    },
}

DEFAULTS = {
    "volume": 25, "mute": False, "bass": 0, "treble": 0, "loudness": True,
    "status_light": True, "buttons_enabled": True, "balance": (100, 100),
    "trueplay": False, "night_mode": False, "dialog_mode": False,
    "speech_enhance_enabled": False, "audio_delay": 0, "surround_enabled": True,
    "surround_full_volume_enabled": False, "surround_volume_tv": 0,
    "surround_volume_music": 0, "sub_enabled": True, "sub_gain": 0,
    "sub_crossover": 80, "fixed_volume": False, "cross_fade": False,
    "shuffle": False, "repeat": False,
}


class SoCoUPnPException(Exception):
    def __init__(self, message, error_code=None):
        super().__init__(message)
        self.error_code = error_code


class NotSupportedException(Exception):
    pass


class Household:
    """Owns the players, the topology and the network-call accounting."""

    def __init__(self, latency=0.0):
        self.players = []
        self.latency = latency
        self.calls = []
        self.inflight = 0
        self.lock = threading.RLock()
        self.favorites = []
        self.alarms = {}
        self.alarm_ids = itertools.count(1)
        self.alarm_list_version = 0
        self.faults = {}

    # -- accounting ------------------------------------------------------
    def call(self, name):
        with self.lock:
            self.inflight += 1
        try:
            if self.latency:
                time.sleep(self.latency)
            with self.lock:
                self.calls.append(name)
            fault = self.faults.get(name)
            if fault is not None:
                if callable(fault):
                    fault()
                else:
                    raise fault
        finally:
            with self.lock:
                self.inflight -= 1

    def reset_calls(self):
        with self.lock:
            self.calls = []

    def fail(self, name, error):
        """Make the next calls named ``name`` raise ``error``."""
        self.faults[name] = error

    # -- topology --------------------------------------------------------
    def add(self, name, model="Sonos Era 100", ip=None, **kwargs):
        ip = ip or f"192.168.1.{10 + len(self.players)}"
        player = SimPlayer(self, name=name, model=model, ip=ip, **kwargs)
        self.players.append(player)
        return player

    def visible(self):
        return [p for p in self.players if p.is_visible]

    def find(self, name):
        return next(p for p in self.players if p.player_name == name)

    def topology(self):
        """A readable snapshot, e.g. ``Kitchen+Bedroom | Office``."""
        groups = {}
        for player in self.visible():
            groups.setdefault(player._coordinator, []).append(player.player_name)
        parts = []
        for coordinator, names in sorted(groups.items(), key=lambda item: item[1]):
            state = next(p for p in self.players if p.uid == coordinator).playback.state
            mark = " [playing]" if state == "PLAYING" else ""
            parts.append("+".join(names) + mark)
        return " | ".join(parts)

    def add_favorite(self, title, uri, item_class="object.item.audioItem.audioBroadcast"):
        favorite = Favorite(title, uri, item_class)
        self.favorites.append(favorite)
        return favorite

    def add_alarm(self, player, start="07:00:00", recurrence="DAILY", enabled=True,
                  volume=20, program_uri=None, program_metadata="", duration=None,
                  play_mode="NORMAL", include_linked_zones=False):
        alarm_id = str(next(self.alarm_ids))
        self.alarms[alarm_id] = {
            "alarm_id": alarm_id, "room_uuid": player.uid,
            "start_time": _clock(start), "duration": _clock(duration) if duration else None,
            "recurrence": recurrence, "enabled": enabled, "volume": volume,
            "program_uri": program_uri, "program_metadata": program_metadata,
            "play_mode": play_mode, "include_linked_zones": include_linked_zones,
        }
        self.alarm_list_version += 1
        return alarm_id


def _clock(text):
    parts = [int(p) for p in str(text).split(":")]
    while len(parts) < 3:
        parts.append(0)
    return datetime.time(*parts[:3])


class ZoneGroupState:
    def __init__(self, household):
        self.household = household
        self.polls = 0

    def clear_cache(self):
        pass

    def poll(self, player):
        self.polls += 1
        self.household.call("zone_group_state.poll")


class Service:
    """Stands in for ``player.avTransport`` and friends."""

    def __init__(self, player):
        self.player = player

    def GetCurrentTransportActions(self, args):
        self.player.household.call("GetCurrentTransportActions")
        return {"Actions": ",".join(self.player.coordinator().actions)}

    def GetMediaInfo(self, args):
        self.player.household.call("GetMediaInfo")
        playback = self.player.coordinator().playback
        return {"CurrentURI": playback.uri, "CurrentURIMetaData": playback.metadata}


class Favorite:
    def __init__(self, title, uri, item_class):
        self.title = title
        self.resource_meta_data = f"<DIDL-Lite><item><dc:title>{title}</dc:title></item></DIDL-Lite>"
        self.reference = types.SimpleNamespace(
            item_class=item_class,
            resources=[types.SimpleNamespace(uri=uri)],
            title=title,
        )


class Playback:
    def __init__(self):
        self.state = "STOPPED"
        self.status = "OK"
        self.uri = ""
        self.metadata = ""
        self.title = ""
        self.artist = ""
        self.position = 0
        self.duration = 0
        self.queue = []
        self.queue_position = 0
        self.actions = None
        self.sleep_timer = None


class SimGroup:
    def __init__(self, coordinator, members):
        self.coordinator = coordinator
        self.members = members

    @property
    def uid(self):
        return self.coordinator.uid

    @property
    def label(self):
        return "+".join(p.player_name for p in self.members)

    @property
    def volume(self):
        self.coordinator.household.call("group.volume")
        return round(sum(p._values["volume"] for p in self.members) / len(self.members))

    @volume.setter
    def volume(self, value):
        self.coordinator.household.call("group.volume=")
        for player in self.members:
            player._values["volume"] = max(0, min(100, int(value)))

    def set_relative_volume(self, delta):
        self.coordinator.household.call("group.set_relative_volume")
        for player in self.members:
            player._values["volume"] = max(0, min(100, player._values["volume"] + delta))
        return round(sum(p._values["volume"] for p in self.members) / len(self.members))

    @property
    def mute(self):
        self.coordinator.household.call("group.mute")
        return all(p._values["mute"] for p in self.members)

    @mute.setter
    def mute(self, value):
        self.coordinator.household.call("group.mute=")
        for player in self.members:
            player._values["mute"] = bool(value)


class SimPlayer:
    """A SoCo-compatible speaker."""

    def __init__(self, household, name, model, ip, visible=True, uid=None):
        object.__setattr__(self, "_ready", False)
        self.household = household
        self.player_name = name
        self.model = model
        self.ip_address = ip
        self.uid = uid or f"RINCON_{abs(hash(name)) % 10**12:012d}"
        self.is_visible = visible
        self._coordinator = self.uid
        self._values = dict(DEFAULTS)
        self._supported = set(MODELS[model]["settings"])
        self._flags = dict(MODELS[model]["flags"])
        self.playback = Playback()
        self.avTransport = Service(self)
        self.renderingControl = Service(self)
        self.zone_group_state = ZoneGroupState(household)
        self.music_library = MusicLibrary(self)
        object.__setattr__(self, "_ready", True)

    def __repr__(self):
        return f"<SimPlayer {self.player_name}>"

    # -- settings routed through the call counter ------------------------
    def __getattr__(self, name):
        if name in SETTING_KEYS:
            player = object.__getattribute__(self, "_ready") and self
            if not player:
                raise AttributeError(name)
            if name not in self._supported:
                raise AttributeError(name)
            self.household.call(f"get {name}")
            return self._values[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if getattr(self, "_ready", False) and name in SETTING_KEYS:
            if name not in self._supported:
                raise AttributeError(name)
            self.household.call(f"set {name}")
            self._values[name] = value
            return
        object.__setattr__(self, name, value)

    # -- capability flags ------------------------------------------------
    # SoCo asks the device for each of these, so they cost a request just like
    # a setting does.  Counting them keeps the responsiveness numbers honest.
    def _flag(self, name, default=False):
        self.household.call(f"capability {name}")
        return self._flags.get(name, default)

    @property
    def is_soundbar(self):
        return self._flag("is_soundbar")

    @property
    def is_arc_ultra_soundbar(self):
        return self._flag("is_arc_ultra")

    @property
    def has_satellites(self):
        return self._flag("has_satellites")

    @property
    def _ht_sat_chan_map(self):
        return self._flag("_ht_sat_chan_map", "")

    @property
    def has_subwoofer(self):
        return self._flag("has_subwoofer")

    @property
    def supports_fixed_volume(self):
        return self._flag("supports_fixed_volume")

    @property
    def is_coordinator(self):
        return self._coordinator == self.uid

    # -- topology --------------------------------------------------------
    def coordinator(self):
        return next(p for p in self.household.players if p.uid == self._coordinator)

    @property
    def group(self):
        self.household.call("group")
        coordinator = self.coordinator()
        members = [p for p in self.household.players if p._coordinator == self._coordinator]
        return SimGroup(coordinator, members)

    @property
    def all_zones(self):
        self.household.call("all_zones")
        return list(self.household.players)

    def join(self, other):
        self.household.call("join")
        self._coordinator = other.coordinator()._coordinator
        self.household.alarm_list_version += 0

    def unjoin(self):
        self.household.call("unjoin")
        followers = [p for p in self.household.players
                     if p._coordinator == self._coordinator and p is not self]
        if self.is_coordinator and followers:
            # Hand coordination to the first remaining member, as Sonos does.
            new = followers[0]
            for player in followers:
                player._coordinator = new.uid
            new.playback = self.playback
            self.playback = Playback()
        self._coordinator = self.uid

    # -- transport -------------------------------------------------------
    def get_current_transport_info(self):
        self.household.call("GetTransportInfo")
        playback = self.coordinator().playback
        return {"current_transport_state": playback.state,
                "current_transport_status": playback.status}

    def get_current_media_info(self):
        self.household.call("GetMediaInfo")
        playback = self.coordinator().playback
        return {"uri": playback.uri, "channel_count": "1"}

    def get_current_track_info(self):
        self.household.call("GetPositionInfo")
        playback = self.coordinator().playback
        return {
            "title": playback.title, "artist": playback.artist, "album": "",
            "uri": playback.uri, "metadata": playback.metadata,
            "playlist_position": str(playback.queue_position),
            "position": _timestamp(playback.position),
            "duration": _timestamp(playback.duration),
        }

    @property
    def actions(self):
        playback = self.playback
        if playback.actions is not None:
            return playback.actions
        if playback.uri.startswith(("x-sonosapi-stream:", "x-rincon-mp3radio:")):
            return ["Play", "Stop"]
        if not playback.uri:
            return ["Stop"]
        return ["Play", "Pause", "Stop", "Next", "Previous", "Seek", "X_DLNA_SeekTime"]

    def play(self):
        self.household.call("Play")
        self.coordinator().playback.state = "PLAYING"

    def pause(self):
        self.household.call("Pause")
        self.coordinator().playback.state = "PAUSED_PLAYBACK"

    def stop(self):
        self.household.call("Stop")
        self.coordinator().playback.state = "STOPPED"

    def next(self):
        self.household.call("Next")
        playback = self.coordinator().playback
        playback.queue_position = min(len(playback.queue) or 1, playback.queue_position + 1)

    def previous(self):
        self.household.call("Previous")
        playback = self.coordinator().playback
        playback.queue_position = max(1, playback.queue_position - 1)

    def seek(self, position=None, **kwargs):
        self.household.call("Seek")
        hours, minutes, seconds = (int(p) for p in str(position).split(":"))
        self.coordinator().playback.position = hours * 3600 + minutes * 60 + seconds

    def play_uri(self, uri, meta="", start=True, **kwargs):
        self.household.call("SetAVTransportURI")
        playback = self.coordinator().playback
        playback.uri, playback.metadata = uri, meta
        playback.title = playback.title or "Stream"
        if start:
            playback.state = "PLAYING"

    def clear_queue(self):
        self.household.call("RemoveAllTracksFromQueue")
        self.coordinator().playback.queue = []

    def add_to_queue(self, item, **kwargs):
        self.household.call("AddURIToQueue")
        playback = self.coordinator().playback
        playback.queue.append(item)
        return len(playback.queue)

    def add_multiple_to_queue(self, items, container=None, **kwargs):
        """One request per batch, the way SoCo chunks AddMultipleURIsToQueue."""
        self.household.call("AddMultipleURIsToQueue")
        self.coordinator().playback.queue.extend(items)
        return len(self.coordinator().playback.queue)

    def play_from_queue(self, index, start=True):
        self.household.call("SetAVTransportURI(queue)")
        playback = self.coordinator().playback
        playback.uri = f"x-rincon-queue:{self.uid}#0"
        playback.queue_position = index + 1
        item = playback.queue[index] if index < len(playback.queue) else None
        playback.title = getattr(item, "title", "") or "Track"
        if start:
            playback.state = "PLAYING"

    # -- misc ------------------------------------------------------------
    def get_speaker_info(self, **kwargs):
        self.household.call("get_speaker_info")
        return {"model_name": self.model, "zone_name": self.player_name,
                "software_version": "90.0"}

    def get_battery_info(self, timeout=3):
        self.household.call("get_battery_info")
        if not self._flags.get("battery"):
            raise NotSupportedException("No battery")
        return {"Level": 82, "PowerSource": "BATTERY", "Health": "GREEN"}

    def get_sleep_timer(self):
        self.household.call("GetRemainingSleepTimerDuration")
        return self.coordinator().playback.sleep_timer

    def set_sleep_timer(self, seconds):
        self.household.call("ConfigureSleepTimer")
        self.coordinator().playback.sleep_timer = seconds


def _timestamp(total):
    return f"{total // 3600}:{total // 60 % 60:02}:{total % 60:02}"


class MusicLibrary:
    def __init__(self, player):
        self.player = player

    def get_sonos_favorites(self, complete_result=False, **kwargs):
        self.player.household.call("get_sonos_favorites")
        return list(self.player.household.favorites)


# ------------------------------------------------------------------- alarms

VALID_RECURRENCE = ("DAILY", "ONCE", "WEEKDAYS", "WEEKENDS")


def is_valid_recurrence(text):
    if text in VALID_RECURRENCE:
        return True
    return (text.startswith("ON_") and len(text) > 3
            and all(c in "0123456" for c in text[3:])
            and len(set(text[3:])) == len(text[3:]))


def build_alarms_module(household, resolve):
    """Return a stand-in for ``soco.alarms`` bound to this household."""

    class Alarm:
        def __init__(self, zone, start_time=None, duration=None, recurrence="DAILY",
                     enabled=True, program_uri=None, program_metadata="",
                     play_mode="NORMAL", volume=20, include_linked_zones=False, **kwargs):
            self.zone = zone
            self.room_uuid = zone.uid if zone else kwargs.get("room_uuid")
            self.start_time = start_time or datetime.time(7, 0)
            self.duration = duration
            self.recurrence = recurrence
            self.enabled = enabled
            self.program_uri = program_uri
            self.program_metadata = program_metadata
            self.play_mode = play_mode
            self.volume = volume
            self.include_linked_zones = include_linked_zones
            self._alarm_id = None

        @property
        def alarm_id(self):
            return self._alarm_id

        def update(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        def save(self):
            if self.zone is None:
                raise RuntimeError("Cannot save alarm: zone is not set.")
            household.call("SaveAlarm")
            record = {
                "room_uuid": self.zone.uid, "start_time": self.start_time,
                "duration": self.duration, "recurrence": self.recurrence,
                "enabled": self.enabled, "volume": self.volume,
                "program_uri": self.program_uri, "program_metadata": self.program_metadata,
                "play_mode": self.play_mode, "include_linked_zones": self.include_linked_zones,
            }
            if self._alarm_id is None:
                self._alarm_id = str(next(household.alarm_ids))
            record["alarm_id"] = self._alarm_id
            household.alarms[self._alarm_id] = record
            household.alarm_list_version += 1
            return self._alarm_id

        def remove(self):
            household.call("DestroyAlarm")
            household.alarms.pop(self._alarm_id, None)
            household.alarm_list_version += 1
            self._alarm_id = None

    class Alarms:
        _instance = None

        def __new__(cls):
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.alarms = {}
                cls._instance.alarms_skipped = {}
                cls._instance.last_alarm_list_version = None
            return cls._instance

        def __iter__(self):
            return iter(list(self.alarms.values()))

        def update(self, zone=None):
            household.call("ListAlarms")
            self.alarms = {}
            self.alarms_skipped = {}
            for alarm_id, record in household.alarms.items():
                zone_for = resolve(record["room_uuid"])
                alarm = Alarm(zone_for, **{k: v for k, v in record.items()
                                           if k not in ("alarm_id", "room_uuid")})
                alarm.room_uuid = record["room_uuid"]
                alarm._alarm_id = alarm_id
                if zone_for is None:
                    self.alarms_skipped[alarm_id] = alarm
                else:
                    self.alarms[alarm_id] = alarm
            self.last_alarm_list_version = household.alarm_list_version

        def update_skipped(self, zone):
            for alarm_id, alarm in list(self.alarms_skipped.items()):
                if zone.uid == alarm.room_uuid:
                    alarm.zone = zone
                    self.alarms[alarm_id] = alarm
                    self.alarms_skipped.pop(alarm_id)

    module = types.ModuleType("soco.alarms")
    module.Alarm = Alarm
    module.Alarms = Alarms
    module.is_valid_recurrence = is_valid_recurrence
    module.reset = lambda: setattr(Alarms, "_instance", None)
    return module


# ----------------------------------------------------- fake ``soco`` package

class DidlResource:
    def __init__(self, uri, protocol_info="", **kwargs):
        self.uri = uri
        self.protocol_info = protocol_info


class DidlMusicTrack:
    def __init__(self, title="", parent_id="0", item_id="", resources=(), **kwargs):
        self.title = title
        self.parent_id = parent_id
        self.item_id = item_id
        self.resources = list(resources)
        self.item_class = "object.item.audioItem.musicTrack"


def build_soco_module(household):
    """Return a stand-in for the vendored ``soco`` package."""
    module = types.ModuleType("soco")

    def SoCo(ip):
        player = next((p for p in household.players if p.ip_address == ip), None)
        if player is None:
            raise OSError(f"No speaker at {ip}")
        return player

    data_structures = types.ModuleType("soco.data_structures")
    data_structures.DidlResource = DidlResource
    data_structures.DidlMusicTrack = DidlMusicTrack

    exceptions = types.ModuleType("soco.exceptions")
    exceptions.SoCoUPnPException = SoCoUPnPException
    exceptions.NotSupportedException = NotSupportedException

    module.SoCo = SoCo
    module.config = types.SimpleNamespace(REQUEST_TIMEOUT=3)
    module.data_structures = data_structures
    module.exceptions = exceptions
    module.discover = lambda **kwargs: list(household.players)
    return module
