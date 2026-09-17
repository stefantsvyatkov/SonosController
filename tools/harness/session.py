"""The emulator: boots the real add-on against fake NVDA and a simulated household."""

import importlib
import queue
import types
import sys
import time
from pathlib import Path

from . import fakewx, nvda
from .sonos import Household, build_alarms_module, build_soco_module

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "globalPlugins.sonosController"


class TrackedQueue(queue.Queue):
    """A task queue that also reports whether the worker is mid-task.

    ``tasks.empty()`` alone is not enough: the worker has already removed the
    item it is working on, so the emulator would stop waiting too early.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.busy = False

    def get(self, *args, **kwargs):
        self.busy = False
        item = super().get(*args, **kwargs)
        self.busy = True
        return item

    def get_nowait(self):
        # Draining on exit is not the worker starting work, so bypass the hook.
        return queue.Queue.get(self, block=False)


class Speech(list):
    """Everything NVDA was asked to speak, in order."""

    def __init__(self):
        super().__init__()
        self.mark = 0

    def drain(self):
        new = self[self.mark:]
        self.mark = len(self)
        return list(new)

    def last(self):
        return self[-1] if self else ""

    def said(self, fragment):
        return any(fragment.lower() in line.lower() for line in self)


class Session:
    """A running add-on, driven by key presses.

    ``network="sim"`` uses the simulated household.  ``network="real"`` leaves
    the vendored SoCo in place so the add-on talks to speakers on the LAN.
    """

    def __init__(self, household=None, network="sim", addresses=(), selected="",
                 secure=False, latency=None):
        self.household = household if household is not None else default_household()
        if latency is not None:
            self.household.latency = latency
        self.network = network
        self.speech = Speech()
        self.callafter = queue.Queue()
        self.clipboard = {}
        self.log = nvda.Log()
        self.dialogs = []
        self._modal_handler = None
        self._last_script = None
        self._saved_modules = {}
        self._install(addresses, selected, secure)

    # ------------------------------------------------------------ set-up
    def _install(self, addresses, selected, secure):
        modules, self.repeat, self.conf = nvda.build(
            self.speech, self.callafter, self.clipboard, self.log)
        self.wx = fakewx.build(self.callafter)
        modules["wx"] = self.wx
        modules["gui"].mainFrame = fakewx.MainFrame()
        modules["globalVars"].appArgs.secure = secure
        self.responder = self.wx.responder
        self.inputCore = modules["inputCore"]

        for name, module in modules.items():
            self._saved_modules[name] = sys.modules.get(name)
            sys.modules[name] = module

        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        for name in [n for n in sys.modules if n.startswith("globalPlugins")]:
            del sys.modules[name]

        self.conf["sonosController"]["selectedRoom"] = selected
        self.conf["sonosController"]["knownAddresses"] = ",".join(addresses)

        self.package = importlib.import_module(PACKAGE)
        self.discovery = importlib.import_module(PACKAGE + ".discovery")
        self.vendors = importlib.import_module(PACKAGE + ".vendors")

        if self.network == "sim":
            self._patch_vendors()
            self._patch_discovery()

        # Swap in the tracking queue before the worker thread is created.
        self.package.queue = types.SimpleNamespace(
            Queue=TrackedQueue, Full=queue.Full, Empty=queue.Empty)

        fakewx.Dialog.modal_hook = self._on_modal
        self.plugin = self.package.GlobalPlugin()

    def _patch_vendors(self):
        household = self.household
        soco_module = build_soco_module(household)
        alarms_module = build_alarms_module(
            household, lambda uid: next((p for p in household.players if p.uid == uid), None))
        alarms_module.reset()
        self.alarms_module = alarms_module
        ifaddr_module = type(sys)("ifaddr")
        ifaddr_module.get_adapters = lambda: []

        self.vendors.load = lambda: (soco_module, ifaddr_module)
        self.vendors.soco = lambda: soco_module
        self.vendors.ifaddr = lambda: ifaddr_module
        self.vendors.soco_submodule = lambda name: (
            alarms_module if name == "alarms" else getattr(soco_module, name))

    def _patch_discovery(self):
        household = self.household
        known = {p.ip_address for p in household.players}
        self.discovery.reachable = lambda ip: ip in known
        self.discovery.discover_rooms = lambda soco, timeout=4: [
            p for p in household.players if p.is_visible]

    def close(self):
        try:
            self.plugin.terminate()
        except Exception:
            pass
        self.pump()
        fakewx.Dialog.modal_hook = None
        for name, module in self._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for name in [n for n in sys.modules if n.startswith("globalPlugins")]:
            del sys.modules[name]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------- modal
    def _on_modal(self, dialog):
        self.dialogs.append(dialog)
        if self._modal_handler is None:
            return fakewx.ID_CANCEL
        handler, self._modal_handler = self._modal_handler, None
        result = handler(dialog)
        return fakewx.ID_OK if result in (None, True, fakewx.ID_OK) else fakewx.ID_CANCEL

    def on_dialog(self, handler):
        """Register a one-shot handler for the next modal dialog."""
        self._modal_handler = handler

    # -------------------------------------------------------------- keys
    def pump(self):
        """Run everything queued with ``wx.CallAfter`` on the main thread."""
        while True:
            try:
                function, args, kwargs = self.callafter.get_nowait()
            except queue.Empty:
                return
            function(*args, **kwargs)

    def settle(self, timeout=15.0):
        """Wait until the worker, the network and the main-thread queue are idle."""
        deadline = time.monotonic() + timeout
        stable = 0
        while time.monotonic() < deadline:
            self.pump()
            idle = (self.plugin.tasks.empty() and not self.plugin.tasks.busy
                    and self.callafter.empty() and self.household.inflight == 0)
            stable = stable + 1 if idle else 0
            if stable >= 4:
                return True
            time.sleep(0.005)
        return False

    def press(self, *keys, settle=True):
        """Send one or more gestures exactly the way NVDA dispatches them."""
        for key in keys:
            gesture = nvda.Gesture(key)
            if self.inputCore.decide_executeGesture.decide(gesture):
                name, script = self.plugin.getScript(gesture)
                self.repeat.note(name, self._last_script)
                self._last_script = name
                if script is not None:
                    script(gesture)
                else:
                    self.speech.append(f"<passed to Windows: {key}>")
                    self._last_script = None
            else:
                self.speech.append(f"<blocked: {key}>")
            if settle:
                self.settle()
        return self.speech.drain()

    def say(self, *keys):
        """Press keys and return the transcript as one readable block."""
        return "\n".join(self.press(*keys))

    def select_room(self, name):
        """Step Right through the groups until ``name`` is the selected one."""
        for _ in range(len(self.household.players) + 1):
            if name in self.speech.last():
                return self.speech.last()
            self.press("kb:rightArrow")
        raise LookupError(f"Could not select {name!r}; heard {self.speech.last()!r}")

    # ------------------------------------------------------------- state
    @property
    def mode(self):
        plugin = self.plugin
        if plugin.mode:
            prefix = "menu:" if plugin.menuMode else ""
            return prefix + plugin.mode
        return "layer" if plugin.active else "off"

    @property
    def bound_keys(self):
        return sorted(self.plugin._gestureMap)

    def calls(self):
        return list(self.household.calls)

    def count_calls(self, reset=True):
        total = len(self.household.calls)
        if reset:
            self.household.reset_calls()
        return total


def default_household(latency=0.0):
    """A small household that exercises most of the add-on."""
    house = Household(latency=latency)
    living = house.add("Living Room", "Sonos Era 100", ip="192.168.1.10")
    kitchen = house.add("Kitchen", "Sonos Era 100", ip="192.168.1.11")
    house.add("Office", "Sonos Move 2", ip="192.168.1.12")
    house.add("TV Room", "Sonos Arc", ip="192.168.1.13")

    living.playback.state = "PLAYING"
    living.playback.uri = "x-rincon-queue:%s#0" % living.uid
    living.playback.title = "Blue Monday"
    living.playback.artist = "New Order"
    living.playback.duration = 268
    living.playback.position = 42
    living.playback.queue_position = 1
    kitchen.playback.state = "STOPPED"

    house.add_favorite("Test Radio", "x-sonosapi-stream:test?sid=254")
    house.add_favorite("Jazz Playlist", "file:///jazz",
                       "object.container.playlistContainer")
    house.add_alarm(living, start="07:30:00", recurrence="WEEKDAYS", volume=18)
    return house
