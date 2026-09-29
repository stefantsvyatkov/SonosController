"""Standard controls for values, with the keys a user expects.

* :class:`Slider` is the native trackbar with its standard keys: Home is the
  minimum, End the maximum, the arrows move one step and Page Up and Page Down
  :data:`PAGE_STEP`.  One style of the control's own is set, ``TBS_DOWNISLEFT``,
  with which the trackbar itself takes Up and Page Up as more (without it Up
  lowers the value).  No key is handled here.
* :class:`NumberChoice` is a standard dropdown of ordered numbers (hours,
  minutes).  While it is closed, Up takes the next larger value, Down the next
  smaller, Home the smallest and End the largest; Page Up and Page Down move
  :data:`PAGE_STEP` values in a list long enough for that.  Open, it is walked as
  it is shown.  Nothing is reported by the add-on: the selection is put silently
  next to the target and the native control is given one arrow key towards it,
  so the control makes the last step itself and raises the value, selection and
  events a screen reader listens for.  BST Radio's NumberComboBox does the same.

Only numbers get :class:`NumberChoice`; rooms, sounds and every other list are
plain ``wx.Choice`` controls.
"""

import ctypes

import wx

from .util import PAGE_STEP

try:
    _user32 = ctypes.windll.user32
except AttributeError:  # not Windows; only reachable from the test harness
    _user32 = None

WM_KEYDOWN = 0x0100
CB_SETCURSEL = 0x014E
CB_GETDROPPEDSTATE = 0x0157
GWL_STYLE = -16
TBS_DOWNISLEFT = 0x0400
VK_UP, VK_DOWN = 0x26, 0x28

#: Lists shorter than this have no pages: Page Up and Page Down do nothing there.
PAGED_LENGTH = 13


class Slider(wx.Slider):
    """The native trackbar: Up and Page Up raise the value, Home is 0, End the maximum."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.SetLineSize(1)
        self.SetPageSize(PAGE_STEP)
        simulate = getattr(self, "set_native_style", None)
        if simulate is not None:      # test double stands in for the native control
            simulate(TBS_DOWNISLEFT)
        elif _user32 is not None:
            handle = self.GetHandle()
            style = _user32.GetWindowLongW(handle, GWL_STYLE)
            _user32.SetWindowLongW(handle, GWL_STYLE, style | TBS_DOWNISLEFT)


class NumberChoice(wx.Choice):
    """A dropdown of ordered numbers, lowest first, where Up gives the next larger one."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._forwarding = False
        self.Bind(wx.EVT_KEY_DOWN, self._onKey)

    def target(self, key):
        """The item ``key`` goes to while the list is closed, or None to leave the key as it is."""
        count, current = self.GetCount(), self.GetSelection()
        page = PAGE_STEP if count >= PAGED_LENGTH else 0
        return {
            wx.WXK_UP: min(current + 1, count - 1),
            wx.WXK_NUMPAD_UP: min(current + 1, count - 1),
            wx.WXK_DOWN: max(current - 1, 0),
            wx.WXK_NUMPAD_DOWN: max(current - 1, 0),
            wx.WXK_HOME: 0,
            wx.WXK_NUMPAD_HOME: 0,
            wx.WXK_END: count - 1,
            wx.WXK_NUMPAD_END: count - 1,
            wx.WXK_PAGEUP: max(current, min(current + page, count - 1)),
            wx.WXK_NUMPAD_PAGEUP: max(current, min(current + page, count - 1)),
            wx.WXK_PAGEDOWN: max(current - page, 0),
            wx.WXK_NUMPAD_PAGEDOWN: max(current - page, 0),
        }.get(key)

    def _onKey(self, event):
        # The forwarded arrow arrives here again; let it through untouched.
        if self._forwarding or event.HasAnyModifiers() or self._isOpen() or not self.GetCount():
            event.Skip()
            return
        target = self.target(event.GetKeyCode())
        if target is None:
            event.Skip()
            return
        self.moveTo(target)

    def moveTo(self, target):
        """Silently next to ``target``, then one native arrow onto it."""
        current = self.GetSelection()
        if target == current:
            return
        start = target - 1 if target > current else target + 1
        simulate = getattr(self, "simulate_key", None)
        if simulate is not None:      # test double stands in for the native control
            if start != current:
                self.SetSelection(start)
            simulate(wx.WXK_DOWN if target > current else wx.WXK_UP)
            return
        handle = self.GetHandle()
        if start != current:
            _user32.SendMessageW(handle, CB_SETCURSEL, start, 0)
        self._forwarding = True
        try:
            _user32.SendMessageW(handle, WM_KEYDOWN, VK_DOWN if target > current else VK_UP, 0)
        finally:
            self._forwarding = False

    def _isOpen(self):
        if getattr(self, "simulate_key", None) is not None:   # the test double's lists never drop down
            return False
        return _user32 is not None and bool(_user32.SendMessageW(self.GetHandle(), CB_GETDROPPEDSTATE, 0, 0))


def numbers(parent, count, start=0):
    """A zero-padded number dropdown, for example 00 to 23."""
    return NumberChoice(parent, choices=[f"{i:02d}" for i in range(start, start + count)])
