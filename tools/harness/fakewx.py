"""A fake ``wx`` that models Win32 control keyboard behaviour.

The point is not to draw anything but to reproduce what a keyboard user
experiences.  In particular the arrow-key direction of each control follows
the documented Win32 behaviour, including the horizontal trackbar, where
Up/Left *decrease* and Down/Right *increase*.
"""

import types

VERTICAL, HORIZONTAL = 8, 4
ALIGN_CENTER_VERTICAL, ALIGN_RIGHT = 0x800, 0x200
EXPAND, ALL, BOTTOM, RIGHT, TOP, LEFT = 0x2000, 0xF0, 0x80, 0x20, 0x10, 0x40
ID_OK, ID_CANCEL, ID_ANY = 5100, 5101, -1
OK, CANCEL, YES_NO, NO_DEFAULT = 4, 16, 10, 0x80
YES, NO = 2, 8
ICON_ERROR, ICON_QUESTION, ICON_INFORMATION = 0x200, 0x400, 0x800
DEFAULT_DIALOG_STYLE, RESIZE_BORDER = 0x400000, 0x40
SL_HORIZONTAL, SL_VERTICAL, SL_LABELS = HORIZONTAL, VERTICAL, 0x10
RA_SPECIFY_ROWS, RA_SPECIFY_COLS = 0x04, 0x08
NOT_FOUND = -1
FD_OPEN, FD_FILE_MUST_EXIST = 1, 0x1000
DD_DEFAULT_STYLE, DD_DIR_MUST_EXIST = 0x10, 0x200
SP_ARROW_KEYS = 0x4

EVT_BUTTON, EVT_CHOICE, EVT_RADIOBOX = "button", "choice", "radiobox"
EVT_CHECKBOX, EVT_TEXT, EVT_SLIDER, EVT_SPINCTRL = "checkbox", "text", "slider", "spinctrl"
EVT_KEY_DOWN = "key_down"
EVT_SCROLL_CHANGED = "scroll_changed"

# wx key codes, matching the real values so add-on code can use wx.WXK_*.
WXK_END, WXK_HOME = 312, 313
WXK_LEFT, WXK_UP, WXK_RIGHT, WXK_DOWN = 314, 315, 316, 317
WXK_NUMPAD_UP, WXK_NUMPAD_PAGEUP, WXK_NUMPAD_PAGEDOWN, WXK_NUMPAD_DOWN = 321, 323, 324, 326
WXK_PAGEUP, WXK_PAGEDOWN = 366, 367
WXK_SPACE, WXK_RETURN, WXK_ESCAPE = 32, 13, 27

#: Emulator key name -> wx key code.
KEY_CODES = {
    "up": WXK_UP, "down": WXK_DOWN, "left": WXK_LEFT, "right": WXK_RIGHT,
    "pageup": WXK_PAGEUP, "pagedown": WXK_PAGEDOWN,
    "home": WXK_HOME, "end": WXK_END, "space": WXK_SPACE,
    "enter": WXK_RETURN, "escape": WXK_ESCAPE,
}
NAMES = {code: name for name, code in KEY_CODES.items()}


class Event:
    def __init__(self, kind, source):
        self.kind, self.source = kind, source
        self.skipped = False

    def Skip(self, skip=True):
        self.skipped = skip

    def SetEventObject(self, obj):
        self.source = obj

    def SetInt(self, value):
        self.value = value


class CommandEvent(Event):
    def __init__(self, typeId=None, id=None):
        super().__init__(typeId, None)
        self.id = id


class KeyEvent(Event):
    def __init__(self, source, code, modifiers=False):
        super().__init__(EVT_KEY_DOWN, source)
        self.code = code
        self.modifiers = modifiers

    def GetKeyCode(self):
        return self.code

    def HasAnyModifiers(self):
        return self.modifiers


class Size:
    def __init__(self, width=0, height=0):
        self.width, self.height = width, height

    @property
    def Width(self):
        return self.width

    @property
    def Height(self):
        return self.height


class Window:
    """Common behaviour: identity, visibility, accessible name, key handling."""

    role = "control"
    focusable = True

    def __init__(self, parent, label="", id=ID_ANY, **kwargs):
        self.parent = parent
        self.id = id
        self._label = label
        self._name = label
        self._shown = True
        self._enabled = True
        self._handlers = {}
        # A Dialog sets ``top`` to itself before calling up, so never clobber it.
        if getattr(self, "top", None) is None:
            self.top = parent.top if isinstance(parent, Window) else parent
        if self.top is not None:
            self.top.register(self)

    # -- wx surface ------------------------------------------------------
    def SetName(self, name):
        self._name = name
        self._explicit_name = True

    def GetName(self):
        return self._name

    def SetLabel(self, label):
        self._label = label
        if not getattr(self, "_explicit_name", False):
            self._name = label

    def GetLabel(self):
        return self._label

    def Show(self, show=True):
        self._shown = bool(show)

    def Hide(self):
        self._shown = False

    def IsShown(self):
        return self._shown

    def Enable(self, enable=True):
        self._enabled = bool(enable)

    def IsEnabled(self):
        return self._enabled

    def SetFocus(self):
        if self.top is not None:
            self.top.focus = self

    def SetMinSize(self, size):
        pass

    def GetSize(self):
        return Size(440, 400)

    def Bind(self, event, handler, id=None):
        self._handlers.setdefault(event, []).append((handler, id))

    def fire(self, event, target=None):
        target = target or self
        for handler, wanted in self._handlers.get(event, []):
            if wanted in (None, ID_ANY, target.id):
                handler(Event(event, target))
        # Events bubble to the dialog, which is where dialogs.py binds EVT_BUTTON.
        if isinstance(self.parent, Window):
            self.parent.fire(event, target)
        elif self.top is not None and self.top is not self:
            self.top.fire(event, target)

    # -- emulator surface ------------------------------------------------
    def visible(self):
        if not self._shown:
            return False
        return self.parent.visible() if isinstance(self.parent, Window) else True

    def speak(self):
        """What NVDA would announce when this control receives focus."""
        value = self.value_text()
        parts = [self._name or self._label, self.role]
        if value:
            parts.append(value)
        if not self._enabled:
            parts.append("unavailable")
        return "  ".join(p for p in parts if p)

    def value_text(self):
        return ""

    def key(self, name):
        """Handle a key press the way wx does: handlers first, then the control."""
        code = KEY_CODES.get(name)
        if code is not None and self._handlers.get(EVT_KEY_DOWN):
            event = KeyEvent(self, code)
            for handler, _wanted in self._handlers[EVT_KEY_DOWN]:
                handler(event)
            if not event.skipped:
                return True
        return self.native_key(name)

    def native_key(self, name):
        """What the underlying Windows control does with the key."""
        return False


class Panel(Window):
    role = "pane"
    focusable = False

    def SetSizer(self, sizer):
        self.sizer = sizer

    def Layout(self):
        pass


class StaticText(Window):
    role = "static text"
    focusable = False

    def value_text(self):
        return self._label


class Choice(Window):
    role = "combo box"

    def __init__(self, parent, choices=(), **kwargs):
        super().__init__(parent, **kwargs)
        self.choices = list(choices)
        self.selection = NOT_FOUND

    def SetSelection(self, index):
        self.selection = index if 0 <= index < len(self.choices) else NOT_FOUND

    def GetSelection(self):
        return self.selection

    def GetStringSelection(self):
        return self.choices[self.selection] if self.selection >= 0 else ""

    def GetCount(self):
        return len(self.choices)

    def value_text(self):
        return self.GetStringSelection()

    def native_key(self, name):
        # Win32 combo box: Down moves to the next item, Up to the previous.
        if name not in ("up", "down", "home", "end"):
            return False
        if not self.choices:
            return True
        if name == "home":
            new = 0
        elif name == "end":
            new = len(self.choices) - 1
        else:
            step = 1 if name == "down" else -1
            new = max(0, min(len(self.choices) - 1, max(self.selection, 0) + step))
        if new != self.selection:
            self.selection = new
            self.fire(EVT_CHOICE)
        return True


class RadioBox(Window):
    role = "grouping"

    def __init__(self, parent, label="", choices=(), majorDimension=0, style=0, **kwargs):
        super().__init__(parent, label=label, **kwargs)
        self.choices = list(choices)
        self.selection = 0
        self.style = style

    def SetSelection(self, index):
        self.selection = index

    def GetSelection(self):
        return self.selection

    def value_text(self):
        return f"{self.choices[self.selection]} radio button" if self.choices else ""

    def native_key(self, name):
        vertical = bool(self.style & RA_SPECIFY_ROWS)
        forward = ("down", "right") if vertical else ("right", "down")
        back = ("up", "left") if vertical else ("left", "up")
        if name in forward:
            new = min(len(self.choices) - 1, self.selection + 1)
        elif name in back:
            new = max(0, self.selection - 1)
        else:
            return False
        if new != self.selection:
            self.selection = new
            self.fire(EVT_RADIOBOX)
        return True


class CheckBox(Window):
    role = "check box"

    def __init__(self, parent, label="", **kwargs):
        super().__init__(parent, label=label, **kwargs)
        self.value = False

    def SetValue(self, value):
        self.value = bool(value)

    def GetValue(self):
        return self.value

    def value_text(self):
        return "checked" if self.value else "not checked"

    def native_key(self, name):
        if name != "space":
            return False
        self.value = not self.value
        self.fire(EVT_CHECKBOX)
        return True


class CheckListBox(Window):
    role = "check list box"

    def __init__(self, parent, choices=(), size=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.choices = list(choices)
        self.checked = [False] * len(self.choices)
        self.selection = 0

    def Check(self, index, value=True):
        self.checked[index] = bool(value)

    def IsChecked(self, index):
        return self.checked[index]

    def GetCount(self):
        return len(self.choices)

    def value_text(self):
        if not self.choices:
            return ""
        state = "checked" if self.checked[self.selection] else "not checked"
        return f"{self.choices[self.selection]}  {state}  {self.selection + 1} of {len(self.choices)}"

    def native_key(self, name):
        if name in ("up", "down"):
            step = 1 if name == "down" else -1
            self.selection = max(0, min(len(self.choices) - 1, self.selection + step))
            return True
        if name == "space":
            self.checked[self.selection] = not self.checked[self.selection]
            return True
        return False


class Slider(Window):
    role = "slider"

    def __init__(self, parent, value=0, minValue=0, maxValue=100, style=SL_HORIZONTAL, **kwargs):
        super().__init__(parent, **kwargs)
        self.value, self.min, self.max, self.style = value, minValue, maxValue, style
        self.page = max(1, (maxValue - minValue) // 10)

    def SetValue(self, value):
        self.value = max(self.min, min(self.max, value))

    def GetValue(self):
        return self.value

    def GetMin(self):
        return self.min

    def GetMax(self):
        return self.max

    def value_text(self):
        return str(self.value)

    def simulate_key(self, code):
        """Apply a wx key code the way the native trackbar would.

        The add-on corrects the inverted arrows by handing the control the key
        it already reads as the wanted direction, so the emulator offers the
        same entry point the real control does.
        """
        return self.native_key(NAMES.get(code, ""))

    def native_key(self, name):
        # Win32 trackbar: VK_LEFT/VK_UP decrement and VK_RIGHT/VK_DOWN
        # increment, with Page Up/Page Down following the same direction.
        # Measured against real wxWidgets in verify_win32_keys.py, so a
        # horizontal slider lowers its value on the Up arrow.
        deltas = {"up": -1, "left": -1, "down": 1, "right": 1,
                  "pageup": -self.page, "pagedown": self.page,
                  "home": self.min - self.value, "end": self.max - self.value}
        if name not in deltas:
            return False
        before = self.value
        self.SetValue(self.value + deltas[name])
        if self.value != before:
            self.fire(EVT_SLIDER)
        return True


class SpinCtrl(Window):
    role = "spin button"

    def __init__(self, parent, value="", min=0, max=100, initial=0, style=0, **kwargs):
        super().__init__(parent, **kwargs)
        self.min, self.max = min, max
        text = str(value).lstrip("-")
        self.value = initial if initial else (int(value) if text.isdigit() else min)

    def SetValue(self, value):
        self.value = max(self.min, min(self.max, int(value)))

    def GetValue(self):
        return self.value

    def value_text(self):
        return str(self.value)

    def native_key(self, name):
        # A spin control follows the natural direction: Up increases.
        deltas = {"up": 1, "down": -1, "pageup": 10, "pagedown": -10,
                  "home": self.min - self.value, "end": self.max - self.value}
        if name not in deltas:
            return False
        before = self.value
        self.SetValue(self.value + deltas[name])
        if self.value != before:
            self.fire(EVT_SPINCTRL)
        return True


class TextCtrl(Window):
    role = "edit"

    def __init__(self, parent, value="", **kwargs):
        super().__init__(parent, **kwargs)
        self.value = value

    def SetValue(self, value):
        self.value = value

    def GetValue(self):
        return self.value

    def value_text(self):
        return self.value or "blank"

    def type(self, text):
        self.value += text


class Button(Window):
    role = "button"

    def native_key(self, name):
        if name not in ("space", "enter"):
            return False
        self.fire(EVT_BUTTON)
        return True


# ------------------------------------------------------------------ sizers

class BoxSizer:
    def __init__(self, orientation=VERTICAL):
        self.orientation = orientation
        self.items = []

    def Add(self, item, proportion=0, flag=0, border=0):
        self.items.append(item)
        return item

    def Show(self, window, show=True, recursive=False):
        if isinstance(window, Window):
            window.Show(show)

    def AddStretchSpacer(self, *a, **k):
        pass

    def ShowItems(self, show):
        for item in self.items:
            if isinstance(item, BoxSizer):
                item.ShowItems(show)
            elif isinstance(item, Window):
                item.Show(show)

    def Layout(self):
        pass

    def walk(self):
        for item in self.items:
            if isinstance(item, BoxSizer):
                yield from item.walk()
            elif isinstance(item, Window):
                yield item


class FlexGridSizer(BoxSizer):
    """Two-column grid.  Only ordering and visibility matter to the emulator."""

    def __init__(self, rows=0, cols=2, vgap=0, hgap=0):
        super().__init__(VERTICAL)
        self.cols = cols
        self.growable = []

    def AddGrowableCol(self, column, proportion=0):
        self.growable.append(column)

    def AddSpacer(self, size):
        self.items.append(None)

    def ShowItems(self, show):
        for item in self.items:
            if isinstance(item, BoxSizer):
                item.ShowItems(show)
            elif isinstance(item, Window):
                item.Show(show)


# ------------------------------------------------------------------ dialogs

class Dialog(Window):
    """Base dialog.  ``ShowModal`` is driven by the emulator, not a message loop."""

    role = "dialog"
    focusable = False
    modal_hook = None  # the emulator sets this to drive dialogs from a test

    def ShowModal(self):
        if Dialog.modal_hook is None:
            return ID_CANCEL
        return Dialog.modal_hook(self)

    def __init__(self, parent, title="", style=0, **kwargs):
        self.top = self
        self.children = []
        self.focus = None
        self.title = title
        self.returncode = ID_CANCEL
        super().__init__(parent, label=title, **kwargs)

    def register(self, window):
        if window is not self:
            self.children.append(window)

    def CreateButtonSizer(self, flags):
        sizer = BoxSizer(HORIZONTAL)
        if flags & OK:
            sizer.Add(Button(self, label="OK", id=ID_OK))
        if flags & CANCEL:
            sizer.Add(Button(self, label="Cancel", id=ID_CANCEL))
        return sizer

    def FindWindowById(self, id):
        return next((c for c in self.children if c.id == id), None)

    def SetSizer(self, sizer):
        self.sizer = sizer

    def SetSizerAndFit(self, sizer):
        self.sizer = sizer

    def Fit(self):
        pass

    def Layout(self):
        pass

    def CentreOnParent(self, direction=0):
        pass

    def Centre(self, direction=0):
        pass

    def EndModal(self, code):
        self.returncode = code

    def Destroy(self):
        pass

    def visible(self):
        return True

    # -- emulator surface ------------------------------------------------
    def tab_order(self):
        return [c for c in self.children if c.focusable and c.visible()]

    def read(self):
        """A transcript of the dialog the way a screen reader would present it."""
        lines = [f"{self.title}  dialog"]
        lines += [f"  {c.speak()}" for c in self.tab_order()]
        return "\n".join(lines)

    def press(self, key, times=1):
        for _ in range(times):
            key = key.lower()
            order = self.tab_order()
            if key == "tab":
                index = order.index(self.focus) if self.focus in order else -1
                self.focus = order[(index + 1) % len(order)]
            elif key in ("shift+tab", "backtab"):
                index = order.index(self.focus) if self.focus in order else 0
                self.focus = order[(index - 1) % len(order)]
            elif key == "escape":
                self.EndModal(ID_CANCEL)
            elif key == "enter" and not isinstance(self.focus, Button):
                ok = self.FindWindowById(ID_OK)
                if ok:
                    ok.fire(EVT_BUTTON)
            elif self.focus is not None:
                self.focus.key(key)
        return self.focus.speak() if self.focus else ""

    def focus_on(self, name):
        for control in self.tab_order():
            if name in (control.GetName(), control.GetLabel()):
                self.focus = control
                return control
        raise LookupError(f"No visible control named {name!r}. Have: "
                          f"{[c.GetName() for c in self.tab_order()]}")


class MainFrame:
    def __init__(self):
        self.popups = 0

    def prePopup(self):
        self.popups += 1

    def postPopup(self):
        self.popups -= 1

    def register(self, window):
        pass


class Responder:
    """Scripted answers for the modal helpers the add-on calls directly."""

    def __init__(self):
        self.message_boxes = []
        self.text_entries = []
        self.file_dialogs = []
        self.message_answer = NO
        self.text_answer = None
        self.file_answer = None


def build(callafter, responder=None):
    """Return a module object usable as ``sys.modules['wx']``."""
    responder = responder or Responder()
    module = types.ModuleType("wx")
    for name, value in list(globals().items()):
        if not name.startswith("_") and name not in ("types", "build"):
            setattr(module, name, value)

    def CallAfter(function, *args, **kwargs):
        callafter.put((function, args, kwargs))

    def MessageBox(message, caption="", style=OK, parent=None):
        responder.message_boxes.append((caption, message, style))
        if style & YES_NO:
            return responder.message_answer
        return OK

    class TextEntryDialog(Dialog):
        def __init__(self, parent, message, caption="", value="", **kwargs):
            super().__init__(parent, title=caption)
            self.message = message
            self.field = TextCtrl(self, value=value)
            self.field.SetName(message)
            responder.text_entries.append(self)

        def ShowModal(self):
            answer = responder.text_answer
            if callable(answer):
                answer = answer(self)
            if answer is None:
                return ID_CANCEL
            self.field.SetValue(answer)
            return ID_OK

        def GetValue(self):
            return self.field.GetValue()

    class _PathDialog(Dialog):
        def __init__(self, parent, message="", wildcard="", style=0, **kwargs):
            super().__init__(parent, title=message)
            responder.file_dialogs.append(self)

        def ShowModal(self):
            return ID_OK if responder.file_answer else ID_CANCEL

        def GetPath(self):
            return responder.file_answer or ""

    module.PostEvent = lambda target, event: target.fire(
        getattr(event, "kind", None) or EVT_SLIDER, target)
    module.CallAfter = CallAfter
    module.CallLater = lambda ms, fn, *a, **k: CallAfter(fn, *a, **k)
    module.MessageBox = MessageBox
    module.TextEntryDialog = TextEntryDialog
    module.FileDialog = _PathDialog
    module.DirDialog = _PathDialog
    module.responder = responder
    return module
