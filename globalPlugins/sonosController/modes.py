"""One description of every keyboard mode the layer can be in.

The layer used to describe each mode three times: an allow-list of gestures in
the input hook, an action mapping when submitting work, and the object the
worker kept.  Those could drift apart.  Everything the input hook and the
submit path need now comes from this table.
"""

from dataclasses import dataclass


# Gesture -> action for the main layer.  ``script_<action>`` handles each one.
KEYS = {
    "kb:leftArrow": "left", "kb:rightArrow": "right",
    "kb:upArrow": "up", "kb:downArrow": "down",
    "kb:m": "mute", "kb:space": "playPause",
    "kb:control+leftArrow": "previous", "kb:control+rightArrow": "next",
    "kb:p": "playing", "kb:b": "battery", "kb:t": "transfer", "kb:escape": "back",
    "kb:g": "group", "kb:enter": "applyGroup",
    "kb:control+s": "settings", "kb:control+p": "playbackSettings",
    "kb:s": "sleepTimer", "kb:f": "favorites", "kb:control+f": "localFiles",
    "kb:u": "url", "kb:a": "alarms", "kb:e": "elapsed", "kb:r": "remaining",
    "kb:n": "newAlarm", "kb:delete": "deleteAlarm",
    "kb:control+shift+leftArrow": "seekBack",
    "kb:control+shift+rightArrow": "seekForward",
}

# Actions that only make sense inside a mode, so the main layer ignores them.
MODE_ONLY = ("applyGroup", "newAlarm", "deleteAlarm")

TOGGLE = "kb:NVDA+shift+control+s"


@dataclass(frozen=True)
class Mode:
    """What a submode accepts and how it renames the layer's actions."""

    #: Action the layer submits to start this mode.
    start: str
    #: Gesture that re-presses the mode's own shortcut, which leaves the mode.
    shortcut: str
    #: Main action -> action the worker understands while in this mode.
    actions: dict
    #: Main actions that keep their ordinary meaning, such as volume.
    keeps: frozenset = frozenset()

    def gestures(self):
        """Every gesture this mode accepts, as raw (un-normalised) identifiers."""
        wanted = set(self.actions) | set(self.keeps)
        allowed = {key for key, action in KEYS.items() if action in wanted}
        allowed.add(self.shortcut)
        allowed.add("kb:escape")
        return allowed

    def translate(self, action):
        """Map a main-layer action into this mode, or ``None`` if it does not fit."""
        if action in self.keeps:
            return action
        return self.actions.get(action)


MENU_MOVES = {"up": "menuUp", "down": "menuDown"}
VOLUME = frozenset({"up", "down"})

MODES = {
    "favorites": Mode(
        start="menuFavoritesStart", shortcut="kb:f",
        actions={**MENU_MOVES, "applyGroup": "menuEnter"}),
    "timer": Mode(
        start="menuTimerStart", shortcut="kb:s",
        actions={**MENU_MOVES, "left": "menuLeft", "right": "menuRight",
                 "applyGroup": "menuEnter"}),
    "alarms": Mode(
        start="menuAlarmsStart", shortcut="kb:a",
        actions={**MENU_MOVES, "playPause": "menuSpace", "applyGroup": "menuEnter",
                 "newAlarm": "menuNew", "deleteAlarm": "menuDelete"}),
    "settings": Mode(
        start="settingsStart", shortcut="kb:control+s",
        actions={"up": "settingsUp", "down": "settingsDown",
                 "left": "settingsLeft", "right": "settingsRight"}),
    "playbackSettings": Mode(
        start="playbackSettingsStart", shortcut="kb:control+p",
        actions={"up": "settingsUp", "down": "settingsDown",
                 "left": "settingsLeft", "right": "settingsRight"}),
    # Room selection uses Left and Right, so Up and Down keep adjusting the
    # volume exactly as they do in the main layer instead of dropping the mode.
    "group": Mode(
        start="groupStart", shortcut="kb:g", keeps=VOLUME,
        actions={"left": "groupLeft", "right": "groupRight",
                 "playPause": "groupToggle", "applyGroup": "groupCommit"}),
    "transfer": Mode(
        start="transferStart", shortcut="kb:t", keeps=VOLUME,
        actions={"left": "transferLeft", "right": "transferRight",
                 "playPause": "transferCommit", "applyGroup": "transferCommit"}),
}

#: Modes whose worker state is a menu object.
MENU_MODES = ("favorites", "timer", "alarms")
#: Modes whose worker state is a settings object.
SETTINGS_MODES = ("settings", "playbackSettings")
#: Modes that mutate room grouping and therefore support cancellation.
ROOM_MODES = ("group", "transfer")
