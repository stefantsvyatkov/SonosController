"""Does a screen reader still hear the value when we move a slider ourselves?

Fixing the inverted arrows is pointless if the corrected movement stops firing
the accessibility event NVDA listens to.  This installs a WinEvent hook and
compares two ways of moving the slider:

1. ``SetValue`` from Python.
2. Posting the key the native trackbar already understands as "increase".

Nothing is injected into the desktop; messages go straight to the control.
"""

import ctypes
from ctypes import wintypes

import wx

user32 = ctypes.windll.user32

EVENT_OBJECT_VALUECHANGE = 0x800E
WINEVENT_OUTOFCONTEXT = 0x0000
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
VK_UP, VK_DOWN = 0x26, 0x28
TBM_GETPOS = 0x0400

WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
    wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

events = []


def _on_event(hook, event, hwnd, obj, child, thread, time_):
    events.append((event, hwnd))


callback = WINEVENTPROC(_on_event)


def pump():
    message = wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))


def value_changes_for(handle):
    pump()
    return [e for e in events if e[0] == EVENT_OBJECT_VALUECHANGE and e[1] == handle]


def main():
    app = wx.App()
    # Off-screen and not activated, so a running screen reader is not disturbed.
    frame = wx.Frame(None, title="slider event probe", pos=(-4000, -4000),
                     size=(300, 120),
                     style=wx.FRAME_TOOL_WINDOW | wx.FRAME_NO_TASKBAR | wx.BORDER_NONE)
    panel = wx.Panel(frame)
    slider = wx.Slider(panel, value=50, minValue=0, maxValue=100,
                       style=wx.SL_HORIZONTAL | wx.SL_LABELS)
    frame.ShowWithoutActivating()   # MSAA events need a realised, shown window
    wx.SafeYield()
    handle = slider.GetHandle()

    hook = user32.SetWinEventHook(
        EVENT_OBJECT_VALUECHANGE, EVENT_OBJECT_VALUECHANGE, None,
        callback, 0, 0, WINEVENT_OUTOFCONTEXT)
    print(f"hook installed: {bool(hook)}")

    print("\n1. native key (Down, which the trackbar treats as increase)")
    events.clear()
    before = user32.SendMessageW(handle, TBM_GETPOS, 0, 0)
    user32.SendMessageW(handle, WM_KEYDOWN, VK_DOWN, 0)
    user32.SendMessageW(handle, WM_KEYUP, VK_DOWN, 0)
    wx.SafeYield()
    after = user32.SendMessageW(handle, TBM_GETPOS, 0, 0)
    native = len(value_changes_for(handle))
    print(f"   {before} -> {after}, value-change events: {native}")

    print("\n2. SetValue from Python")
    events.clear()
    before = slider.GetValue()
    slider.SetValue(before + 1)
    wx.SafeYield()
    after = slider.GetValue()
    programmatic = len(value_changes_for(handle))
    print(f"   {before} -> {after}, value-change events: {programmatic}")

    print("\nCONCLUSION:")
    if programmatic:
        print("  SetValue fires the value-change event, so a corrected key")
        print("  handler is announced normally. Handle the keys in Python.")
    elif native:
        print("  Only the native key path fires the event. Re-post the key the")
        print("  trackbar already understands instead of calling SetValue.")
    else:
        print("  Neither path fired an event here; the probe is inconclusive")
        print("  and this needs checking with NVDA running.")

    user32.UnhookWinEvent(hook)
    frame.Destroy()
    app.Destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
