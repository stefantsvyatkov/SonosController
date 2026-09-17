"""Controls that behave the way a keyboard user expects on Windows.

The Win32 trackbar behind ``wx.Slider`` decrements on Up and Page Up and
increments on Down and Page Down, so a horizontal slider runs backwards for
anyone driving it from the keyboard.

``Slider`` below stays an ordinary slider - same look, same mouse behaviour,
same value NVDA reads - and only turns the vertical keys the right way round.
It does that by handing the native control the key it already treats as the
wanted direction, rather than setting the value from Python: a programmatic
``SetValue`` does not raise the accessibility value-change event, so the
correction would otherwise be silent for a screen reader.
"""

import ctypes

import wx

try:
    _user32 = ctypes.windll.user32
except AttributeError:  # not Windows; only reachable from the test harness
    _user32 = None

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101

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


class Slider(wx.Slider):
    """A slider whose Up and Page Up raise the value, as users expect."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._forwarding = False
        self.Bind(wx.EVT_KEY_DOWN, self._onKey)

    def _onKey(self, event):
        # The forwarded key arrives here again; let it through untouched.
        if self._forwarding:
            event.Skip()
            return
        wanted = SWAP.get(event.GetKeyCode())
        if wanted is None or event.HasAnyModifiers():
            event.Skip()
            return
        self._forwarding = True
        try:
            self._forward(wanted)
        finally:
            self._forwarding = False

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
