"""Check the emulator's key model against real Windows controls.

The emulator claims that a horizontal ``wx.Slider`` moves the wrong way under
the arrow keys.  That claim is only worth anything if it matches the real
control, so this script builds the real widgets and posts key messages
*directly to each control's window handle*.  Nothing is shown on screen and
no input is injected into the desktop, so a running screen reader and the
user's keyboard focus are left alone.

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
    """The control's own window plus any child windows it is built from.

    wx.SpinCtrl on Windows is an edit control paired with an up-down control,
    so a message sent only to the outer handle is ignored.
    """
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
    spin = wx.SpinCtrl(panel, min=0, max=100, initial=50)
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
    print("wx.SpinCtrl, starting at 50")
    moved = False
    for key in ("up", "down"):
        before = spin.GetValue()
        tap(spin, key)
        after = spin.GetValue()
        moved = moved or after != before
        arrow = "increases" if after > before else "decreases" if after < before else "no change"
        print(f"  {key:<9} {before:>3} -> {after:>3}   {arrow}")
    if not moved:
        print("  (inconclusive: the spin control only reacts to keys routed")
        print("   through its edit buddy, which posted messages do not reach.")
        print("   Confirm this one by hand in a visible dialog.)")

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
    app.Destroy()
    return 0 if inverted else 1


if __name__ == "__main__":
    raise SystemExit(main())
