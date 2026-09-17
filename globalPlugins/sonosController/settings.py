"""Model- and capability-filtered local Sonos settings."""

from dataclasses import dataclass
import logging

from .i18n import _


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    low: int = 0
    high: int = 1
    gate: str = "common"
    kind: str = "number"
    step: int = 1


OPTIONS = (
    # Translators: speaker setting names, spoken while browsing Control+S.
    Option("bass", _("Bass"), -10, 10),
    Option("treble", _("Treble"), -10, 10),
    Option("loudness", _("Loudness"), kind="switch"),
    Option("status_light", _("Status light"), kind="switch"),
    Option("buttons_enabled", _("Touch controls"), kind="switch"),
    Option("volume", _("Room volume"), 0, 100),
    Option("mute", _("Room mute"), kind="switch"),
    Option("balance", _("Balance"), -100, 100, kind="balance"),
    Option("trueplay", _("Trueplay calibration enabled"), kind="switch"),
    Option("night_mode", _("Night mode"), gate="soundbar", kind="switch"),
    Option("dialog_mode", _("Speech enhancement"), gate="legacySoundbar", kind="switch"),
    Option("speech_enhance_enabled", _("Speech enhancement"), gate="ultra", kind="switch"),
    Option("audio_delay", _("TV dialog sync"), 0, 5, gate="soundbar"),
    Option("surround_enabled", _("Surround speakers"), gate="surround", kind="switch"),
    Option("surround_full_volume_enabled", _("Surround music mode"), gate="surround",
           kind="surroundMode"),
    Option("surround_volume_tv", _("Surround TV level"), -15, 15, gate="surround"),
    Option("surround_volume_music", _("Surround music level"), -15, 15, gate="surround"),
    Option("sub_enabled", _("Subwoofer"), gate="sub", kind="switch"),
    Option("sub_gain", _("Subwoofer level"), -15, 15, gate="sub"),
    Option("sub_crossover", _("Subwoofer crossover Hz"), 50, 110, gate="amp"),
    Option("fixed_volume", _("Fixed line output"), gate="fixed", kind="switch"),
)

# Error codes a speaker returns when it simply does not offer a setting.
UNSUPPORTED_CODES = ("401", "402", "501", "600", "601", "602")


def capability(room, key):
    try:
        return bool(getattr(room, key))
    except AttributeError:
        return False


class Settings:
    """Reads every supported setting once, then works from that snapshot.

    Re-reading over the network on every key press made browsing settings slow
    on a grouped system, so the value is cached and refreshed only when this
    add-on is the one that changed it.
    """

    def __init__(self, controller):
        self.items = []
        self.values = {}
        self.models = {}
        self.skipped = []
        self.unavailable = []
        for room in controller.members(controller.current()):
            try:
                info = room.get_speaker_info()
                model = info.get("model_name") or info.get("model_number") or _("Unknown model")
            except AttributeError:
                model = _("Unknown model")
            self.models[room.uid] = model
            # Once per room: each capability lookup is a request on real
            # hardware, so asking again for all twenty-odd options was waste.
            gates = self.gates(room, model)
            for option in OPTIONS:
                if not gates[option.gate]:
                    continue
                try:
                    value = self.read(room, option)
                    if value is None:
                        self.skipped.append((room.uid, option.key, "unsupported or not configured"))
                        continue
                    if not option.low <= value <= option.high:
                        self.skipped.append((room.uid, option.key, "value outside known range"))
                        continue
                except Exception as error:
                    unsupported = (isinstance(error, AttributeError)
                                   or type(error).__name__ == "NotSupportedException"
                                   or str(getattr(error, "error_code", "")) in UNSUPPORTED_CODES)
                    if not unsupported:
                        self.unavailable.append((room.uid, option.key))
                        logging.getLogger(__name__).warning(
                            "Setting read failed: %s %s", model, option.key, exc_info=True)
                    self.skipped.append((room.uid, option.key, type(error).__name__))
                    continue
                self.items.append((room, option))
                self.values[(room.uid, option.key)] = value
        if not self.items:
            raise RuntimeError(_("No supported settings available."))
        self.index = 0
        # The room and model are only worth repeating when they change.
        self._spokenRoom = None

    @staticmethod
    def gates(room, model):
        soundbar = capability(room, "is_soundbar")
        ultra = capability(room, "is_arc_ultra_soundbar")
        # SoCo 0.31.1 exposes satellite channel mappings after topology polling.
        satellites = capability(room, "has_satellites")
        mapping = getattr(room, "_ht_sat_chan_map", "") or ""
        surround = soundbar and satellites and any(channel in mapping for channel in (":LR", ":RR"))
        gates = {"common": True, "soundbar": soundbar, "ultra": ultra,
                 "legacySoundbar": soundbar and not ultra, "surround": surround,
                 "sub": capability(room, "has_subwoofer"),
                 "amp": model.lower().endswith("sonos amp"),
                 "coordinator": capability(room, "is_coordinator"), "fixed": False}
        if any(name in model.lower() for name in ("port", "connect", "zp80", "zp90")):
            gates["fixed"] = capability(room, "supports_fixed_volume")
        return gates

    @staticmethod
    def read(room, option):
        value = getattr(room, option.key)
        if value is None:
            return None
        if option.kind == "balance":
            left, right = value
            return right - left
        return int(value)

    def value(self):
        room, option = self.items[self.index]
        return self.values.get((room.uid, option.key))

    def describe(self, context=None):
        """Describe the current setting.

        ``context`` names the room and model.  Left unset it is announced only
        when the selection moved to a different room, so walking through one
        speaker's settings does not repeat its name on every key press.
        """
        room, option = self.items[self.index]
        value = self.value()
        if value is None:
            return _("{room}, {label}, no longer available. Reopen settings.").format(
                room=room.player_name, label=option.label)
        if option.kind == "switch":
            text = _("on") if value else _("off")
        elif option.kind == "surroundMode":
            text = _("full") if value else _("ambient")
        elif option.kind == "balance":
            if value == 0:
                text = _("center")
            else:
                side = _("right") if value > 0 else _("left")
                text = f"{abs(value)} {side}"
        else:
            text = str(value)
        if context is None:
            context = room.uid != self._spokenRoom
        prefix = ""
        if context:
            self._spokenRoom = room.uid
            prefix = f"{room.player_name}, {self.models[room.uid]}, "
        return f"{prefix}{option.label}, {text}"

    def move(self, delta):
        self.index = (self.index + delta) % len(self.items)
        return self.describe()

    def adjust(self, delta):
        room, option = self.items[self.index]
        current = self.value()
        if current is None:
            raise RuntimeError(_("This setting is no longer available. Reopen settings."))
        value = max(option.low, min(option.high, current + delta * option.step))
        if value == current:
            return self.describe(context=False)
        if option.kind == "balance":
            room.balance = (100 - max(0, value), 100 + min(0, value))
        else:
            setattr(room, option.key,
                    bool(value) if option.kind in ("switch", "surroundMode") else value)
        # Trust the write, but re-read once so a rejected change is not announced
        # as if it had been applied.
        try:
            confirmed = self.read(room, option)
        except Exception:
            confirmed = value
        self.values[(room.uid, option.key)] = value if confirmed is None else confirmed
        return self.describe(context=False)
