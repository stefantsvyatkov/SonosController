"""Drive the real AlarmDialog with real wxPython.

The emulator says the volume slider is fixed and the packaged bytecode says
NVDA loaded that code, yet the dialog still felt unchanged in use.  This builds
the actual dialog - not a bare slider - and reports what every field does with
the arrow keys, so the remaining disagreement is settled with measurements
rather than argument.

Windows are off-screen and never activated, so a running screen reader and the
user's keyboard focus are untouched.

    .tools/harness-venv/Scripts/python.exe tools/harness/probe_alarm_dialog.py
"""

import ctypes
import sys
import types
from datetime import time as clock_time
from pathlib import Path

import wx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

user32 = ctypes.windll.user32
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
VK = {"up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
      "pageup": 0x21, "pagedown": 0x22}


def tap(window, key):
    handle = window.GetHandle()
    user32.SendMessageW(handle, WM_KEYDOWN, VK[key], 0)
    user32.SendMessageW(handle, WM_KEYUP, VK[key], 0)
    wx.SafeYield()


def value_of(control):
    if isinstance(control, wx.Slider):
        return control.GetValue()
    if isinstance(control, wx.Choice):
        return control.GetStringSelection()
    return None


def fake_context():
    """The smallest context AlarmDialog will accept."""
    alarm = types.SimpleNamespace(
        start_time=clock_time(7, 30), duration=None, recurrence="WEEKDAYS",
        enabled=True, volume=18, include_linked_zones=False,
        program_uri=None, program_metadata="", play_mode="NORMAL",
        room_uuid="RINCON_1", zone=types.SimpleNamespace(player_name="Living Room"),
        alarm_id="1")
    return {"alarm": alarm, "rooms": [], "roomNames": ["Living Room", "Kitchen"],
            "roomIds": ["RINCON_1", "RINCON_2"], "favorites": [],
            "favoriteNames": [], "defaultRoom": "RINCON_1"}


def main():
    from support import load

    PAGE_STEP = load("controls").PAGE_STEP

    # ``ui`` is only touched when a field appears; a stub keeps that path alive.
    messages = []
    ui = types.ModuleType("ui")
    ui.message = messages.append
    sys.modules.setdefault("ui", ui)

    dialogs = load("dialogs")

    app = wx.App()
    parent = wx.Frame(None, pos=(-4000, -4000), size=(10, 10),
                      style=wx.FRAME_TOOL_WINDOW | wx.FRAME_NO_TASKBAR)
    parent.ShowWithoutActivating()

    dialog = dialogs.AlarmDialog(parent, fake_context())
    dialog.SetPosition((-4000, -4000))
    dialog.Show()
    wx.SafeYield()

    print(f"wxPython {wx.version()}\n")
    print(f"{'field':<16} {'control':<13} {'start':>6}  {'Up':<7} {'Down x2':<7}"
          f" {'PgUp':<8} PgDn x2")
    print("-" * 76)

    findings = {}
    for key in ("timeHour", "timeMinute", "volume", "durationMode", "recurrence"):
        control = dialog.controls.get(key)
        if control is None:
            continue
        control.SetFocus()
        wx.SafeYield()
        start = value_of(control)
        tap(control, "up")
        after_up = value_of(control)
        tap(control, "down")
        tap(control, "down")
        after_down = value_of(control)
        tap(control, "down")          # back to the starting value
        tap(control, "pageup")
        after_pageup = value_of(control)
        tap(control, "pagedown")
        tap(control, "pagedown")
        after_pagedown = value_of(control)
        kind = type(control).__name__
        print(f"{key:<16} {kind:<13} {str(start):>6}  {str(after_up):<7} {str(after_down):<7}"
              f" {str(after_pageup):<8} {after_pagedown}")
        findings[key] = (start, after_up, after_down, after_pageup, after_pagedown)

    print()
    start, up, down, pageup, pagedown = findings["volume"]
    if up > start:
        print("  volume slider: Up RAISES it - the correction is live")
    else:
        print("  volume slider: Up still LOWERS it - the correction is NOT working")

    hour_start, hour_up = findings["timeHour"][:2]
    print(f"  hour dropdown: Up moves {hour_start} -> {hour_up}"
          f"  ({'earlier' if hour_up < hour_start else 'later'} hour)")
    hour_pageup = findings["timeHour"][3]
    print(f"  page keys: volume reaches {pageup} and hour {hour_pageup} on Page Up,"
          f"\n             a page being {PAGE_STEP} steps in the arrows' direction")

    print("\n  announcements while building:", messages or "none (as intended)")

    dialog.Destroy()
    parent.Destroy()
    app.Destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
