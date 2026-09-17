"""Fake NVDA modules, faithful enough to run the add-on unmodified.

Only the surface the add-on actually touches is implemented.  Where NVDA's
behaviour matters for a bug (gesture normalisation, the order in which
``decide_executeGesture`` runs relative to script lookup, Win32 control key
handling) it is reproduced rather than simplified.
"""

import types


# ---------------------------------------------------------------- gestures

def normalizeGestureIdentifier(identifier):
    """Lower-case the gesture and sort its modifiers, as NVDA does."""
    source, _, key = identifier.partition(":")
    parts = key.lower().split("+")
    modifiers, main = sorted(parts[:-1]), parts[-1]
    return f"{source.lower()}:{'+'.join(modifiers + [main])}"


MODIFIERS = {"control", "shift", "alt", "nvda", "windows"}


class Gesture:
    """Stands in for ``KeyboardInputGesture``."""

    def __init__(self, identifier, layout="desktop"):
        self.identifier = identifier
        normalized = normalizeGestureIdentifier(identifier)
        source, _, key = normalized.partition(":")
        # NVDA reports both the layout-qualified and the generic identifier.
        self.normalizedIdentifiers = [f"{source}({layout}):{key}", normalized]
        self.isModifier = key in MODIFIERS

    def __repr__(self):
        return f"<Gesture {self.identifier}>"


class Decider:
    """A minimal ``extensionPoints.Decider``."""

    def __init__(self):
        self.handlers = []

    def register(self, handler):
        self.handlers.append(handler)

    def unregister(self, handler):
        self.handlers.remove(handler)

    def decide(self, gesture):
        return all(handler(gesture) is not False for handler in self.handlers)


# ------------------------------------------------------------ scriptHandler

def script(description="", gesture=None, category=None, **kwargs):
    def decorate(function):
        function.__doc__ = description or function.__doc__
        function.scriptInfo = {"description": description, "gesture": gesture,
                               "category": category, **kwargs}
        return function
    return decorate


class ScriptRepeat:
    """Tracks double-press detection for ``getLastScriptRepeatCount``."""

    def __init__(self):
        self.count = 0

    def note(self, name, previous):
        self.count = self.count + 1 if name == previous else 0
        return self.count


# ------------------------------------------------------- globalPluginHandler

class ScriptableObject:
    """The parts of NVDA's ScriptableObject the add-on relies on."""

    def __init__(self):
        self._gestureMap = {}
        self._classGestures = {}
        for name in dir(type(self)):
            if not name.startswith("script_"):
                continue
            info = getattr(getattr(type(self), name), "scriptInfo", None)
            if info and info.get("gesture"):
                identifier = normalizeGestureIdentifier(info["gesture"])
                self._classGestures[identifier] = name[len("script_"):]
        self._gestureMap.update(self._classGestures)

    def bindGestures(self, gestures):
        for identifier, scriptName in gestures.items():
            self._gestureMap[normalizeGestureIdentifier(identifier)] = scriptName

    def removeGestureBinding(self, identifier):
        del self._gestureMap[normalizeGestureIdentifier(identifier)]

    def clearGestureBindings(self):
        self._gestureMap.clear()

    def getScript(self, gesture):
        for identifier in gesture.normalizedIdentifiers:
            name = self._gestureMap.get(identifier)
            if name:
                return name, getattr(self, "script_" + name)
        return None, None

    def terminate(self):
        pass


# ------------------------------------------------------------------- config

class Section(dict):
    pass


class Conf(dict):
    def __init__(self):
        super().__init__()
        self.spec = {}

    def __getitem__(self, key):
        if key not in self:
            super().__setitem__(key, Section())
        return super().__getitem__(key)


# ---------------------------------------------------------------------- log

class Log:
    def __init__(self):
        self.records = []

    def _record(self, level, message, args):
        try:
            text = message % args if args else message
        except Exception:
            text = f"{message} {args}"
        self.records.append((level, text))

    def debug(self, message, *args, **kwargs):
        self._record("debug", message, args)

    def info(self, message, *args, **kwargs):
        self._record("info", message, args)

    def warning(self, message, *args, **kwargs):
        self._record("warning", message, args)

    def error(self, message, *args, **kwargs):
        self._record("error", message, args)

    def exception(self, message="", *args, **kwargs):
        import traceback
        self._record("exception", f"{message}\n{traceback.format_exc()}", args)


# ------------------------------------------------------------------ modules

def build(speech, callafter, clipboard, log):
    """Return the ``sys.modules`` entries that stand in for NVDA."""
    from . import fakewx

    conf = Conf()

    ui = types.ModuleType("ui")
    ui.message = lambda text, *a, **k: speech.append(str(text))
    ui.browseableMessage = lambda text, *a, **k: speech.append(str(text))

    api = types.ModuleType("api")

    def copyToClip(text, *a, **k):
        clipboard["value"] = text
        return True

    api.copyToClip = copyToClip
    api.getClipData = lambda: clipboard.get("value", "")

    inputCore = types.ModuleType("inputCore")
    inputCore.decide_executeGesture = Decider()
    inputCore.normalizeGestureIdentifier = normalizeGestureIdentifier

    repeat = ScriptRepeat()
    scriptHandler = types.ModuleType("scriptHandler")
    scriptHandler.script = script
    scriptHandler.getLastScriptRepeatCount = lambda: repeat.count

    globalPluginHandler = types.ModuleType("globalPluginHandler")
    globalPluginHandler.GlobalPlugin = ScriptableObject

    globalVars = types.ModuleType("globalVars")
    globalVars.appArgs = types.SimpleNamespace(secure=False)

    configModule = types.ModuleType("config")
    configModule.conf = conf

    logHandler = types.ModuleType("logHandler")
    logHandler.log = log

    tones = types.ModuleType("tones")
    tones.beep = lambda hz, ms, *a, **k: speech.append(f"<beep {hz}Hz {ms}ms>")

    wx = fakewx.build(callafter)

    gui = types.ModuleType("gui")
    gui.mainFrame = fakewx.MainFrame()

    addonHandler = types.ModuleType("addonHandler")

    def initTranslation():
        """Install a pass-through ``_`` in the caller's namespace, as NVDA does."""
        import sys as _sys
        _sys._getframe(1).f_globals.setdefault("_", lambda text: text)

    addonHandler.initTranslation = initTranslation

    modules = {
        "ui": ui, "api": api, "inputCore": inputCore, "scriptHandler": scriptHandler,
        "globalPluginHandler": globalPluginHandler, "globalVars": globalVars,
        "config": configModule, "logHandler": logHandler, "tones": tones,
        "wx": wx, "gui": gui, "addonHandler": addonHandler,
    }
    return modules, repeat, conf
