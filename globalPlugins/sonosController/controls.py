"""Controls whose arrow keys mean what a user expects.

Two Windows controls disagree with "Up means more":

* A horizontal trackbar (``wx.Slider``) decrements on Up and Page Up.
* A list control (``wx.Choice``) walks towards the top of the list on Up, so a
  list of numbers counts *down* when the user presses Up.

Both are corrected the same way: the key is swapped and handed back to the
native control, which then moves itself and raises the accessibility events a
screen reader listens for.  Setting the value from Python instead would move
the control silently - a programmatic ``SetValue`` raises no value-change
event, so NVDA would say nothing.

Page Up and Page Down move by :data:`PAGE_STEP` in the same direction as the
arrows, so the two pairs never disagree.
"""

import ctypes

import wx

try:
    _user32 = ctypes.windll.user32
except AttributeError:  # not Windows; only reachable from the test harness
    _user32 = None

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101

#: How far Page Up and Page Down move, in steps of the control's own unit.
PAGE_STEP = 5

#: The wx key each inverted key should behave as.
SWAP = {
    wx.WXK_UP: wx.WXK_DOWN,
    wx.WXK_DOWN: wx.WXK_UP,
    wx.WXK_NUMPAD_UP: wx.WXK_DOWN,
    wx.WXK_NUMPAD_DOWN: wx.WXK_UP,
    wx.WXK_PAGEUP: wx.WXK_PAGEDOWN,
    wx.WXK_PAGEDOWN: wx.WXK_PAGEUP,
    wx.WXK_NUMPAD_PAGEUP: wx.WXK_PAGEDOWN,
    wx.WXK_NUMPAD_PAGEDOWN: wx.WXK_PAGEUP,
}

#: Virtual-key code to send for each wx key.
VIRTUAL_KEYS = {
    wx.WXK_UP: 0x26, wx.WXK_DOWN: 0x28,
    wx.WXK_PAGEUP: 0x21, wx.WXK_PAGEDOWN: 0x22,
}

PAGE_KEYS = (wx.WXK_PAGEUP, wx.WXK_PAGEDOWN,
             wx.WXK_NUMPAD_PAGEUP, wx.WXK_NUMPAD_PAGEDOWN)


class UpMeansMore:
    """Swap the vertical keys of a control that counts the wrong way.

    Mix in before the wx class and call :meth:`correctVerticalKeys` once the
    control exists.
    """

    def correctVerticalKeys(self):
        self._forwarding = False
        self.Bind(wx.EVT_KEY_DOWN, self._onKey)

    def _onKey(self, event):
        # The forwarded key arrives here again; let it through untouched.
        if getattr(self, "_forwarding", False):
            event.Skip()
            return
        code = event.GetKeyCode()
        wanted = SWAP.get(code)
        if wanted is None or event.HasAnyModifiers():
            event.Skip()
            return
        self._forwarding = True
        try:
            self._page(wanted) if code in PAGE_KEYS else self._forward(wanted)
        finally:
            self._forwarding = False

    def _page(self, key):
        """Move a page, which this add-on defines as PAGE_STEP single steps."""
        step = wx.WXK_DOWN if key == wx.WXK_PAGEDOWN else wx.WXK_UP
        for _ in range(PAGE_STEP):
            self._forward(step)

    def _forward(self, key):
        """Let the native control apply ``key`` and report it as it normally would."""
        simulate = getattr(self, "simulate_key", None)
        if simulate is not None:      # test double stands in for the native control
            simulate(key)
            return
        code = VIRTUAL_KEYS[key]
        handle = self.GetHandle()
        _user32.SendMessageW(handle, WM_KEYDOWN, code, 0)
        _user32.SendMessageW(handle, WM_KEYUP, code, 0)


class Slider(UpMeansMore, wx.Slider):
    """A slider whose Up and Page Up raise the value."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # One native page event instead of five line events keeps the speech to
        # a single announcement.
        self.SetPageSize(PAGE_STEP)
        self.correctVerticalKeys()

    def _page(self, key):
        # The trackbar has a real page size, so let it do the whole move.
        self._forward(key)


class NumberChoice(UpMeansMore, wx.Choice):
    """A dropdown of numbers where Up counts up.

    Ordinary lists keep their usual behaviour; this is only for fields whose
    entries are quantities, such as an hour or a minute, where walking towards
    the top of the list reads as lowering the value.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.correctVerticalKeys()


def numbers(parent, count, start=0):
    """A zero-padded number dropdown, for example 00 to 23."""
    return NumberChoice(parent, choices=[f"{i:02d}" for i in range(start, start + count)])
