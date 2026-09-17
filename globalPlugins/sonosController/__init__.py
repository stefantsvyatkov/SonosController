"""Persistent keyboard layer for local Sonos control."""

import queue
import threading

import config
import globalPluginHandler
import globalVars
import inputCore
import scriptHandler
import ui
import wx
from logHandler import log
from scriptHandler import script

from .backend import Controller
from .discovery import RoomDiscovery
from .grouping import Grouping
from .i18n import _
from .localfiles import LocalFiles
from .menus import AlarmMenu, Favorites, SleepTimer
from .modes import KEYS, MENU_MODES, MODE_ONLY, MODES, ROOM_MODES, SETTINGS_MODES, TOGGLE
from .playbacksettings import PlaybackSettings
from .relayhooks import Relay
from .settings import Settings
from .transfer import Transfer
from .util import PAGE_STEP, valid_url
from . import vendors

CONFIG_SECTION = "sonosController"
CONFIG_SPEC = {
    "selectedRoom": "string(default='')",
    "knownAddresses": "string(default='')",
    # Only needed when FFmpeg is installed somewhere unusual; the add-on
    # otherwise finds it on PATH or in the common install locations.
    "ffmpegPath": "string(default='')",
}
# Registered at import time, before any profile reads the section.
config.conf.spec[CONFIG_SECTION] = CONFIG_SPEC


def _normalize(gestures):
    return {inputCore.normalizeGestureIdentifier(key) for key in gestures}


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    # Translators: the category the add-on's commands appear under in NVDA's
    # Input gestures dialog.
    scriptCategory = _("Sonos Controller")

    #: Gestures accepted in the main layer, normalised once at class level.
    _mainGestures = _normalize(key for key, action in KEYS.items()
                               if action not in MODE_ONLY)
    _modeGestures = {name: _normalize(mode.gestures()) for name, mode in MODES.items()}
    _toggleGesture = inputCore.normalizeGestureIdentifier(TOGGLE)

    def __init__(self):
        super().__init__()
        self.active = False
        self.closed = False
        self.generation = 0
        self.mode = None
        self.tasks = queue.Queue(maxsize=8)
        self.stop = threading.Event()
        self.dialogOpen = False
        self.localFiles = LocalFiles()
        self.transferCancel = threading.Event()
        self.worker = None
        self._inputHookRegistered = False
        settings = config.conf[CONFIG_SECTION]
        # Older installations do not have the address cache yet.
        self.selected = settings.get("selectedRoom", "")
        self.relay = Relay(settings.get("ffmpegPath", ""))
        self.knownAddresses = [address for address in settings.get("knownAddresses", "").split(",")
                               if address]
        if globalVars.appArgs.secure:
            self.clearGestureBindings()
            return
        self.worker = threading.Thread(target=self._work, name="SonosController", daemon=True)
        self.worker.start()
        inputCore.decide_executeGesture.register(self._onGesture)
        self._inputHookRegistered = True

    # -- mode state ------------------------------------------------------
    # One field holds the mode.  These read-only views keep the older names
    # meaningful for anything that inspects the layer.

    @property
    def menuMode(self):
        return self.mode if self.mode in MENU_MODES else None

    @property
    def settingsMode(self):
        return self.mode in SETTINGS_MODES

    @property
    def groupMode(self):
        return self.mode == "group"

    @property
    def transferMode(self):
        return self.mode == "transfer"

    def _onGesture(self, gesture):
        if not self.active or self.closed or self.dialogOpen or gesture.isModifier:
            return True
        identifiers = gesture.normalizedIdentifiers
        if not any(identifier.startswith("kb") for identifier in identifiers):
            return True
        allowed = set(self._modeGestures[self.mode] if self.mode else self._mainGestures)
        allowed.add(self._toggleGesture)
        if allowed.intersection(identifiers):
            return True
        # An unrelated key leaves the layer; a main-layer command inside a mode
        # returns to the layer first and then runs as usual.
        if self.mode and self._mainGestures.intersection(identifiers):
            self.script_back(None)
        else:
            self.script_exit(None)
        return True

    def _remember(self, uid):
        if not self.closed:
            config.conf[CONFIG_SECTION]["selectedRoom"] = uid

    def _rememberAddresses(self, addresses):
        if not self.closed:
            config.conf[CONFIG_SECTION]["knownAddresses"] = ",".join(addresses)

    def _cancelled(self, generation):
        """A single cancellation test shared by every long worker operation."""
        return lambda: generation != self.generation or self.stop.is_set()

    def _work(self):
        controller = None
        transfer = None
        grouping = None
        settings = None
        menu = None
        while not self.stop.is_set():
            item = self.tasks.get()
            if item is None:
                return
            generation, action = item
            payload = None
            if isinstance(action, tuple):
                action, payload = action
            if generation != self.generation:
                continue
            cancelled = self._cancelled(generation)
            message = None
            try:
                if controller is None:
                    soco, _ifaddr = vendors.load()
                    discovery = RoomDiscovery(soco, self.knownAddresses,
                                              lambda addresses: wx.CallAfter(self._rememberAddresses, addresses))
                    controller = Controller(discovery, self.selected,
                                            lambda uid: wx.CallAfter(self._remember, uid))
                if action in ("menuFavoritesStart", "menuTimerStart", "menuAlarmsStart"):
                    factory = {"menuFavoritesStart": Favorites, "menuTimerStart": SleepTimer,
                               "menuAlarmsStart": AlarmMenu}[action]
                    menu = factory(controller)
                    prefix = {"menuFavoritesStart": _("Favorites. "),
                              "menuTimerStart": _("Sleep timer. "),
                              "menuAlarmsStart": _("Alarms. ")}[action]
                    message = prefix + menu.describe()
                elif action.startswith("menu"):
                    if menu is None:
                        continue
                    if isinstance(menu, SleepTimer):
                        if action == "menuEnter":
                            message = menu.apply()
                            menu = None
                            wx.CallAfter(self._finishMode, generation)
                        elif action in ("menuUp", "menuDown"):
                            message = menu.move(1 if action == "menuDown" else -1)
                        elif action in ("menuLeft", "menuRight"):
                            message = menu.adjust(1 if action == "menuRight" else -1)
                        elif action in ("menuPageUp", "menuPageDown"):
                            step = PAGE_STEP if action == "menuPageUp" else -PAGE_STEP
                            message = menu.adjust(step)
                        else:
                            continue
                    elif action in ("menuUp", "menuDown"):
                        message = menu.move(1 if action == "menuDown" else -1)
                    elif action in ("menuPageUp", "menuPageDown"):
                        message = menu.move(PAGE_STEP if action == "menuPageDown" else -PAGE_STEP)
                    elif isinstance(menu, Favorites) and action == "menuEnter":
                        self.relay.stop()
                        message = menu.play(cancelled)
                        menu = None
                        wx.CallAfter(self._finishMode, generation)
                    elif isinstance(menu, AlarmMenu):
                        if action == "menuSpace":
                            message = menu.toggle()
                        elif action in ("menuEnter", "menuNew"):
                            context = menu.editor(new=action == "menuNew")
                            wx.CallAfter(self._showAlarm, generation, context)
                            continue
                        elif action == "menuSave":
                            message = menu.save(payload)
                            menu = None
                            wx.CallAfter(self._finishMode, generation)
                        elif action == "menuDeleteConfirmed":
                            message = menu.delete(payload)
                        elif action == "menuDelete":
                            alarm = menu.current()
                            if alarm is None:
                                message = _("Select an alarm.")
                            else:
                                wx.CallAfter(self._confirmDelete, generation,
                                             alarm.alarm_id, menu.describe())
                                continue
                        else:
                            continue
                    else:
                        continue
                elif action == "localStart":
                    player = controller.current().coordinator
                    wx.CallAfter(self._showLocal, generation, player, player.player_name)
                    continue
                elif action == "localPlay":
                    player, selection = payload
                    self.relay.stop()
                    message = self.localFiles.play(player, selection, cancelled)
                elif action == "urlStart":
                    urlPlayer = controller.current().coordinator
                    wx.CallAfter(self._showUrl, generation, urlPlayer.player_name, urlPlayer)
                    continue
                elif action == "urlPlay":
                    urlPlayer, url = payload
                    if not valid_url(url):
                        raise RuntimeError(_("Enter an HTTP or HTTPS audio URL."))
                    message = self.relay.play_url(
                        urlPlayer.group.coordinator, url, cancelled,
                        lambda text: wx.CallAfter(self._announce, generation, text),
                        [room.ip_address for room in controller.rooms])
                elif action in ("settingsStart", "playbackSettingsStart"):
                    settings = (PlaybackSettings(controller) if action == "playbackSettingsStart"
                                else Settings(controller))
                    message = _("Settings. ") + settings.describe()
                    if settings.unavailable:
                        message += _(". Some settings unavailable.")
                elif action.startswith("settings"):
                    if settings is None:
                        continue
                    if action in ("settingsUp", "settingsDown"):
                        message = settings.move(1 if action == "settingsDown" else -1)
                    elif action in ("settingsPageUp", "settingsPageDown"):
                        step = PAGE_STEP if action == "settingsPageUp" else -PAGE_STEP
                        message = settings.adjust(step)
                    else:
                        message = settings.adjust(1 if action == "settingsRight" else -1)
                elif action == "groupStart":
                    grouping = Grouping(controller, self._roomCancel(cancelled))
                    message = (_("Group. ") + grouping.describe()
                               + _(". Space to select, Enter to apply."))
                elif action.startswith("group"):
                    if grouping is None or grouping.cancelled():
                        continue
                    if action == "groupCommit":
                        message = grouping.commit()
                        grouping = None
                        wx.CallAfter(self._finishMode, generation)
                    elif action == "groupToggle":
                        message = grouping.toggle()
                    else:
                        message = grouping.move(1 if action == "groupRight" else -1)
                elif action == "transferStart":
                    transfer = Transfer(controller, self._roomCancel(cancelled))
                    message = transfer.describe()
                elif action.startswith("transfer"):
                    if transfer is None or transfer.cancelled():
                        continue
                    if action == "transferCommit":
                        message = transfer.commit()
                        transfer = None
                        wx.CallAfter(self._finishMode, generation)
                    else:
                        message = transfer.move(1 if action == "transferRight" else -1)
                else:
                    message = controller.execute(action)
            except Exception as error:
                log.exception("Sonos operation failed")
                if action in ("settingsStart", "playbackSettingsStart",
                              "menuFavoritesStart", "menuTimerStart", "menuAlarmsStart"):
                    if action.startswith("menu"):
                        menu = None
                    wx.CallAfter(self._finishMode, generation)
                if action.startswith("transfer"):
                    transfer = None
                    wx.CallAfter(self._finishMode, generation)
                if action.startswith("group"):
                    grouping = None
                    wx.CallAfter(self._finishMode, generation)
                # A rejected playback command does not invalidate discovered rooms.
                code = getattr(error, "error_code", None)
                if code is not None:
                    message = _("Command unavailable. Sonos error {code}.").format(code=code)
                else:
                    message = (str(error) if isinstance(error, (RuntimeError, ValueError))
                               else _("Command failed. Try again."))
            if isinstance(message, dict) and "copy" in message:
                wx.CallAfter(self._copyTrack, generation, message["copy"])
            elif message:
                wx.CallAfter(self._announce, generation, message)

    def _roomCancel(self, cancelled):
        """Grouping and transfer also stop when the user presses Escape."""
        token = self.transferCancel
        return lambda: token.is_set() or cancelled()

    def _finishMode(self, generation):
        if generation == self.generation:
            self.mode = None

    def _copyTrack(self, generation, text):
        if not self.closed and self.active and generation == self.generation:
            import api
            result = api.copyToClip(text)
            ui.message(_("Copied.") if result is not False else _("Copy failed."))

    def _announce(self, generation, message):
        if message and not self.closed and self.active and generation == self.generation:
            ui.message(message)

    def _submit(self, action):
        if not self.active or self.closed:
            return
        mode = self.mode
        if mode is not None and action != MODES[mode].start:
            action = MODES[mode].translate(action)
            if action is None:
                # Nothing in this mode handles the key; fall back to the layer.
                self.script_back(None)
                return
        if not self._enqueue(action) and action.endswith("Start"):
            self.mode = None

    def _enqueue(self, action):
        if self.closed or not self.active:
            return False
        try:
            self.tasks.put_nowait((self.generation, action))
            return True
        except queue.Full:
            ui.message(_("Sonos is busy"))
            return False

    def _drain(self):
        while True:
            try:
                self.tasks.get_nowait()
            except queue.Empty:
                return

    def _unbind(self):
        for key in KEYS:
            try:
                self.removeGestureBinding(key)
            except KeyError:
                pass

    # -- layer control ---------------------------------------------------

    @script(
        # Translators: description of the command that turns the Sonos layer on and off.
        description=_("Toggle the Sonos command layer"),
        gesture=TOGGLE,
    )
    def script_toggle(self, gesture):
        if self.active:
            self.script_exit(gesture)
            return
        self.active = True
        self.generation += 1
        self.bindGestures(KEYS)
        ui.message(_("Connecting."))
        self._submit("open")

    def script_back(self, gesture):
        if self.mode is None:
            self.script_exit(gesture)
            return
        self.transferCancel.set()
        self.mode = None
        self.generation += 1
        self._drain()
        ui.message(_("Sonos."))

    def script_exit(self, gesture):
        wasRoomMode = self.mode in ROOM_MODES
        self.transferCancel.set()
        self.mode = None
        self.active = False
        self.generation += 1
        self._drain()
        self._unbind()
        wx.CallAfter(ui.message,
                     _("Cancelling. Sonos off.") if wasRoomMode else _("Sonos off."))

    # -- main layer commands ---------------------------------------------

    def script_left(self, gesture):
        self._submit("left")

    def script_right(self, gesture):
        self._submit("right")

    def script_up(self, gesture):
        self._submit("up")

    def script_down(self, gesture):
        self._submit("down")

    def script_mute(self, gesture):
        self._submit("mute")

    def script_playPause(self, gesture):
        self._submit("playPause")

    def script_previous(self, gesture):
        self._submit("previous")

    def script_next(self, gesture):
        self._submit("next")

    def script_playing(self, gesture):
        repeat = getattr(scriptHandler, "getLastScriptRepeatCount", lambda: 0)()
        self._submit("copyTrack" if repeat else "playing")

    def script_battery(self, gesture):
        self._submit("battery")

    def script_elapsed(self, gesture):
        self._submit("elapsed")

    def script_remaining(self, gesture):
        self._submit("remaining")

    def script_pageUp(self, gesture):
        self._submit("pageUp")

    def script_pageDown(self, gesture):
        self._submit("pageDown")

    def script_seekBack(self, gesture):
        self._submit("seekBack")

    def script_seekForward(self, gesture):
        self._submit("seekForward")

    def script_localFiles(self, gesture):
        self._submit("localStart")

    def script_url(self, gesture):
        self._submit("urlStart")

    def script_applyGroup(self, gesture):
        self._submit("applyGroup")

    def script_newAlarm(self, gesture):
        self._submit("newAlarm")

    def script_deleteAlarm(self, gesture):
        self._submit("deleteAlarm")

    # -- modes -----------------------------------------------------------

    def _startMode(self, name):
        """Enter a mode, or leave it when its own shortcut is pressed again."""
        if self.mode is not None:
            self.script_back(None)
            return
        if name in ROOM_MODES:
            self.transferCancel = threading.Event()
        self.mode = name
        self._submit(MODES[name].start)

    def script_transfer(self, gesture):
        self._startMode("transfer")

    def script_group(self, gesture):
        self._startMode("group")

    def script_settings(self, gesture):
        self._startMode("settings")

    def script_playbackSettings(self, gesture):
        self._startMode("playbackSettings")

    def script_favorites(self, gesture):
        self._startMode("favorites")

    def script_sleepTimer(self, gesture):
        self._startMode("timer")

    def script_alarms(self, gesture):
        self._startMode("alarms")

    # -- dialogs ---------------------------------------------------------

    def _modal(self, generation, callback):
        if self.closed or not self.active or self.dialogOpen or generation != self.generation:
            return
        import gui
        self.dialogOpen = True
        self._unbind()
        gui.mainFrame.prePopup()
        try:
            callback(gui.mainFrame)
        finally:
            gui.mainFrame.postPopup()
            self.dialogOpen = False
            if self.active and not self.closed:
                self.bindGestures(KEYS)

    def _showLocal(self, generation, player, room):
        def show(parent):
            from .dialogs import LocalFilesDialog
            dialog = LocalFilesDialog(parent, room)
            try:
                if dialog.ShowModal() == wx.ID_OK and generation == self.generation:
                    self._enqueue(("localPlay", (player, dialog.result)))
            finally:
                dialog.Destroy()
        self._modal(generation, show)

    def _showUrl(self, generation, room, player):
        def show(parent):
            import api
            try:
                clipboard = api.getClipData()
            except Exception:
                clipboard = ""
            initial = clipboard.strip() if valid_url(clipboard) else ""
            dialog = wx.TextEntryDialog(
                parent, _("Audio address"),
                _("Play URL on {room}").format(room=room), initial)
            try:
                while dialog.ShowModal() == wx.ID_OK:
                    url = dialog.GetValue().strip()
                    if valid_url(url):
                        if generation == self.generation:
                            self._enqueue(("urlPlay", (player, url)))
                        break
                    wx.MessageBox(_("Enter an HTTP or HTTPS audio URL."),
                                  _("Invalid URL"), wx.OK | wx.ICON_ERROR, parent)
            finally:
                dialog.Destroy()
        self._modal(generation, show)

    def _showAlarm(self, generation, context):
        def show(parent):
            from .dialogs import AlarmDialog
            dialog = AlarmDialog(parent, context)
            try:
                if dialog.ShowModal() == wx.ID_OK and generation == self.generation:
                    self._enqueue(("menuSave", (dialog.result, context)))
            finally:
                dialog.Destroy()
        self._modal(generation, show)

    def _confirmDelete(self, generation, alarm_id, description):
        def show(parent):
            answer = wx.MessageBox(
                _("Delete {alarm}?").format(alarm=description), _("Delete alarm"),
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, parent)
            if answer == wx.YES and generation == self.generation:
                self._enqueue(("menuDeleteConfirmed", alarm_id))
        self._modal(generation, show)

    # -- shutdown --------------------------------------------------------

    def terminate(self):
        if self.closed:
            return
        self.closed = True
        self.active = False
        self.generation += 1
        self.stop.set()
        self.transferCancel.set()
        self.relay.stop()
        if self._inputHookRegistered:
            try:
                inputCore.decide_executeGesture.unregister(self._onGesture)
            except Exception:
                log.exception("Could not unregister the Sonos input hook")
            self._inputHookRegistered = False
        self.clearGestureBindings()
        # Make room for the sentinel so a waiting worker always wakes up.
        self._drain()
        try:
            self.tasks.put_nowait(None)
        except queue.Full:
            pass
        if self.worker is not None and self.worker.is_alive():
            # A request already in flight cannot be interrupted, but the worker
            # is a daemon that stops at its next loop, and nothing it still
            # holds has to be released here.  Waiting longer would only freeze
            # NVDA, so note it and move on.
            self.worker.join(timeout=1.0)
            if self.worker.is_alive():
                log.debug("Sonos worker is finishing a request in the background")
        self._closeInBackground(self.localFiles.close, "SonosAudioShutdown",
                                "Sonos local audio server")
        self._closeInBackground(self.relay.close, "SonosRelayShutdown", "Sonos relay")
        super().terminate()

    @staticmethod
    def _closeInBackground(close, name, label):
        """Give a shutdown a moment, then let it finish on its own thread."""
        thread = threading.Thread(target=close, name=name, daemon=True)
        thread.start()
        thread.join(timeout=1.0)
        if thread.is_alive():
            log.debug("%s is closing in the background", label)
        return thread
