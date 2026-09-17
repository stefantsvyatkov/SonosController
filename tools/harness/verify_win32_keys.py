"""Check the key handling against real Windows controls, not just the emulator.

Two things are measured here:

1. That a stock horizontal ``wx.Slider`` really does move the wrong way under
   the arrow keys, which is what the emulator models and what made the alarm
   dialog's volume field run backwards.
2. That a stock ``wx.Choice`` of numbers counts down when the user presses Up,
   which is why the alarm's hour and minute felt backwards.
3. That the add-on's corrected ``Slider`` and ``NumberChoice`` move the right
   way, five at a time on the page keys, while staying an ordinary slider and
   an ordinary dropdown.

Key messages go *directly to each control's window handle* and the windows are
off-screen and never activated, so a running screen reader and the user's
keyboard focus are left alone.

Run with the harness virtual environment::

    .tools/harness-venv/Scripts/python.exe tools/harness/verify_win32_keys.py
"""

import ctypes

import wx

user32 = ctypes.windll.user32
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
KEYS = {"up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "pageup": 0x21, "pagedown": 0x22}
TBM_GETPOS = 0x0400


ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def handles(window):
    """The control's own window plus any child windows it is built from."""
    found = [window.GetHandle()]

    def collect(child, _param):
        found.append(child)
        return True

    user32.EnumChildWindows(window.GetHandle(), ENUMPROC(collect), None)
    return found


def tap(window, key):
    for handle in handles(window):
        user32.SendMessageW(handle, WM_KEYDOWN, KEYS[key], 0)
        user32.SendMessageW(handle, WM_KEYUP, KEYS[key], 0)


def slider_position(slider):
    return user32.SendMessageW(slider.GetHandle(), TBM_GETPOS, 0, 0)


def main():
    app = wx.App()
    # Never shown: the controls exist as real Win32 windows all the same.
    frame = wx.Frame(None, title="offscreen probe")
    panel = wx.Panel(frame)

    slider = wx.Slider(panel, value=50, minValue=0, maxValue=100,
                       style=wx.SL_HORIZONTAL | wx.SL_LABELS)
    choice = wx.Choice(panel, choices=[f"{i:02d}" for i in range(24)])
    choice.SetSelection(7)

    results = []

    print(f"wxPython {wx.version()}")
    print()
    print("wx.Slider, horizontal, starting at 50")
    for key in ("up", "down", "left", "right", "pageup", "pagedown"):
        before = slider_position(slider)
        tap(slider, key)
        after = slider_position(slider)
        arrow = "increases" if after > before else "decreases" if after < before else "no change"
        print(f"  {key:<9} {before:>3} -> {after:>3}   {arrow}")
        results.append((key, after - before))

    print()
    print("wx.Choice of 00..23, starting at index 7")
    for key in ("up", "down"):
        before = choice.GetSelection()
        tap(choice, key)
        after = choice.GetSelection()
        arrow = "later item" if after > before else "earlier item" if after < before else "no change"
        print(f"  {key:<9} {before:>3} -> {after:>3}   {arrow}")

    deltas = dict(results)
    inverted = deltas.get("up", 0) < 0 and deltas.get("down", 0) > 0
    print()
    print("CONCLUSION:")
    if inverted:
        print("  A horizontal wx.Slider really does move DOWN on the Up arrow.")
        print("  The emulator's model matches Windows, and the alarm dialog's")
        print("  volume slider is inverted for the user.")
    else:
        print("  The slider behaved conventionally here; the emulator's model")
        print("  needs revisiting.")
    if deltas.get("pageup", 0) < 0:
        print("  Page Up moves the same way as the Up arrow, so the whole")
        print("  control is inverted rather than merely inconsistent.")

    frame.Destroy()
    fixed = verify_corrected_controls()
    app.Destroy()
    return 0 if inverted and fixed else 1


def verify_corrected_controls():
    """Drive the add-on's own controls to prove the correction works for real."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "tests"))
    from support import load

    controls = load("controls")

    frame = wx.Frame(None, title="fixed controls probe", pos=(-4000, -4000),
                     size=(300, 120),
                     style=wx.FRAME_TOOL_WINDOW | wx.FRAME_NO_TASKBAR | wx.BORDER_NONE)
    panel = wx.Panel(frame)
    slider = controls.Slider(panel, value=50, minValue=0, maxValue=100,
                             style=wx.SL_HORIZONTAL | wx.SL_LABELS)
    hours = controls.numbers(panel, 24)
    hours.SetSelection(7)
    frame.ShowWithoutActivating()
    wx.SafeYield()

    print()
    print(f"the add-on's controls, page step {controls.PAGE_STEP}")
    ok = True
    for name, control, read, start in (
            ("Slider", slider, lambda: slider_position(slider), 50),
            ("NumberChoice", hours, hours.GetSelection, 7)):
        print(f"  {name}, starting at {start}")
        moves = {}
        for key in ("up", "down", "pageup", "pagedown"):
            control.SetFocus()
            wx.SafeYield()
            before = read()
            tap(control, key)
            wx.SafeYield()
            after = read()
            moves[key] = after - before
            print(f"    {key:<9} {before:>3} -> {after:>3}   {after - before:+d}")
        expected = {"up": 1, "down": -1,
                    "pageup": controls.PAGE_STEP, "pagedown": -controls.PAGE_STEP}
        if moves != expected:
            ok = False
            print(f"    UNEXPECTED: wanted {expected}, got {moves}")

    print()
    print("  Up adds and Page Up adds five, on both controls" if ok
          else "  THE CORRECTION IS NOT WORKING on the real controls")

    frame.Destroy()
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
