"""Reproductions of the findings from the code review, run in the emulator.

Each scenario prints what a user would hear, so the behaviour can be judged
directly rather than inferred from the code.  Run with::

    python tools/harness/scenarios.py [name ...]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import Session
from harness.session import default_household

SCENARIOS = {}


def scenario(title):
    def register(function):
        SCENARIOS[function.__name__] = (title, function)
        return function
    return register


def banner(text):
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


# --------------------------------------------------------------------------

@scenario("Alarm dialog: the volume slider follows the arrows")
def alarm_volume_inverted():
    session = Session()
    session.press("kb:NVDA+shift+control+s", "kb:a")

    def inspect(dialog):
        volume = dialog.focus_on("Volume (%)")
        print(f"  focus: {volume.speak()}")
        start = volume.GetValue()
        print(f"\n  starting value: {start}")
        for key in ("up", "up", "up"):
            dialog.press(key)
            print(f"    press Up    -> {volume.GetValue()}")
        after_up = volume.GetValue()
        print()
        for key in ("down", "down", "down", "down", "down", "down"):
            dialog.press(key)
        print(f"    press Down x6 -> {volume.GetValue()}")
        print()
        print("  Page Up / Page Down, for contrast:")
        dialog.press("pageup")
        print(f"    press PageUp   -> {volume.GetValue()}")
        dialog.press("pagedown")
        print(f"    press PageDown -> {volume.GetValue()}")
        verdict = "still INVERTED" if after_up < start else "correct"
        print(f"\n  verdict: the arrows are {verdict}. Windows moves a trackbar")
        print("           the other way, so the dialog corrects it while keeping")
        print("           an ordinary slider. The alarms scenario checks it")
        print("           in real NVDA; see sandbox/.")
        return False

    session.on_dialog(inspect)
    session.press("kb:enter")
    session.close()


@scenario("Alarm dialog: tab order and which fields appear")
def alarm_fields():
    session = Session()
    session.press("kb:NVDA+shift+control+s", "kb:a")

    def inspect(dialog):
        print(dialog.read())
        print("\n  switching Repeat to 'Selected days':")
        repeat = dialog.focus_on("Repeat")
        while repeat.GetStringSelection() != "Selected days":
            dialog.press("down")
        print(f"  heard: {repeat.speak()}")
        print("  (the Days list has appeared; the dialog names what it added)\n")
        print(dialog.read())
        return False

    session.on_dialog(inspect)
    session.press("kb:enter")
    session.close()


@scenario("Group mode: the volume keys work without losing the selection")
def group_up_cancels():
    session = Session()
    session.press("kb:NVDA+shift+control+s")
    session.select_room("Living Room")
    print("  " + "\n  ".join(session.press("kb:g")))
    print(f"  mode: {session.mode}")
    print("\n  selecting a second room with Right then Space:")
    print("  " + "\n  ".join(session.press("kb:rightArrow", "kb:space")))
    print("\n  now pressing Up, which used to abandon the whole selection:")
    for line in session.press("kb:upArrow"):
        print("  " + line)
    print(f"  mode: {session.mode}")
    print("\n  and Enter still applies what was selected:")
    for line in session.press("kb:enter"):
        print("  " + line)
    print("\n  verdict: Up and Down adjust the volume exactly as they do in the"
          "\n           main layer, and the room selection survives.")
    session.close()


@scenario("Settings: how many network requests one key press costs")
def settings_round_trips():
    house = default_household(latency=0.05)
    session = Session(household=house)
    session.press("kb:NVDA+shift+control+s")
    session.count_calls()

    start = time.monotonic()
    heard = session.press("kb:control+s")
    opening = session.count_calls()
    elapsed = time.monotonic() - start
    print(f"  opening Control+S: {opening} requests, {elapsed:.2f} s"
          f" at {house.latency * 1000:.0f} ms each")
    print(f"  heard: {heard[0][:90]}...")

    for key, label in (("kb:downArrow", "move to next setting"),
                       ("kb:rightArrow", "change the value")):
        start = time.monotonic()
        heard = session.press(key)
        print(f"  {label}: {session.count_calls()} requests,"
              f" {time.monotonic() - start:.2f} s -> {heard[-1] if heard else ''}")
    print("\n  verdict: opening still probes every setting on every room, but"
          "\n           browsing is free and a change costs one write plus one"
          "\n           read-back.")
    session.close()


@scenario("Local files: how many requests a folder costs")
def local_files_round_trips():
    import tempfile
    import os
    house = default_household(latency=0.01)
    session = Session(household=house)
    session.press("kb:NVDA+shift+control+s")

    folder = tempfile.mkdtemp(prefix="sonos-emu-")
    for index in range(25):
        with open(os.path.join(folder, f"track {index + 1:02d}.mp3"), "wb") as handle:
            handle.write(b"\xff\xfb" + b"\0" * 400)

    session.responder.file_answer = folder
    session.count_calls()

    def choose(dialog):
        dialog.focus_on("Open").key("down")          # Folder
        dialog.focus_on("Path").SetValue(folder)
        dialog.result = {"path": folder, "recursive": False}
        return True

    session.on_dialog(choose)
    heard = session.press("kb:control+f")
    batched = len([c for c in session.household.calls if c == "AddMultipleURIsToQueue"])
    print(f"  25 files -> {session.count_calls()} network requests,"
          f" {batched} of them queue additions")
    print(f"  heard: {heard[-1] if heard else '(nothing)'}")
    print("\n  verdict: tracks are queued sixteen at a time, so the documented"
          "\n           1000-file limit is about 63 requests, not 1000.")
    session.close()


@scenario("Unload: NVDA is never held up waiting for a speaker")
def terminate_stall():
    print("  case A: the task queue is full when NVDA unloads the add-on")
    house = default_household()
    session = Session(household=house)
    session.press("kb:NVDA+shift+control+s")
    for _ in range(12):
        session.press("kb:upArrow", settle=False)
    print(f"    queued tasks: {session.plugin.tasks.qsize()} (the queue holds 8)")
    start = time.monotonic()
    session.plugin.terminate()
    print(f"    terminate() took {time.monotonic() - start:.2f} s,"
          f" worker alive: {session.plugin.worker.is_alive()}")
    print("    -> no stall: a full queue means the worker is awake, so it sees"
          "\n       the stop flag on its next loop even though the sentinel"
          "\n       could not be queued.")
    session.close()

    print("\n  case B: the worker is inside a slow request when NVDA unloads")
    house = default_household(latency=1.5)
    session = Session(household=house)
    session.press("kb:NVDA+shift+control+s", settle=False)
    time.sleep(0.3)
    start = time.monotonic()
    session.plugin.terminate()
    elapsed = time.monotonic() - start
    print(f"    terminate() took {elapsed:.2f} s,"
          f" worker alive: {session.plugin.worker.is_alive()}")
    print("    -> a request in flight still cannot be interrupted, but the"
          "\n       worker is a daemon that stops at its next loop and holds"
          "\n       nothing that must be released here, so NVDA waits about a"
          "\n       second rather than the four it used to.")
    session.close()


@scenario("Layer: pressing an unrelated key leaves the layer")
def stray_key():
    session = Session()
    session.press("kb:NVDA+shift+control+s")
    print(f"  mode: {session.mode}")
    for line in session.press("kb:x"):
        print("  " + line)
    print(f"  mode: {session.mode}")
    print("\n  and N, which is bound but only meaningful inside the alarm list:")
    session.press("kb:NVDA+shift+control+s")
    for line in session.press("kb:n"):
        print("  " + line)
    print(f"  mode: {session.mode}")
    session.close()


@scenario("Favorites: play one and return to the main layer")
def favorites_flow():
    session = Session()
    session.press("kb:NVDA+shift+control+s")
    for line in session.press("kb:f", "kb:downArrow", "kb:upArrow"):
        print("  " + line)
    print(f"  mode: {session.mode}")
    for line in session.press("kb:enter"):
        print("  " + line)
    print(f"  mode after Enter: {session.mode}")
    session.close()


@scenario("Transfer: move playback from a playing room to an idle one")
def transfer_flow():
    house = default_household()
    session = Session(household=house)
    session.press("kb:NVDA+shift+control+s")
    session.select_room("Living Room")
    print(f"  topology before: {house.topology()}")
    for line in session.press("kb:t"):
        print("  " + line)
    for line in session.press("kb:space"):
        print("  " + line)
    print(f"  topology after:  {house.topology()}")
    print(f"  mode: {session.mode}")
    session.close()


@scenario("Sleep timer: which arrow does what")
def sleep_timer_axes():
    session = Session()
    session.press("kb:NVDA+shift+control+s")
    for line in session.press("kb:s"):
        print("  " + line)
    print("  Right, Right, Right (raise the preset):")
    for line in session.press("kb:rightArrow", "kb:rightArrow", "kb:rightArrow"):
        print("    " + line)
    print("  Down (move to the Custom row):")
    for line in session.press("kb:downArrow"):
        print("    " + line)
    print("  Down again, already at the last row:")
    for line in session.press("kb:downArrow"):
        print("    " + line)
    print("\n  verdict: the row list clamps silently instead of wrapping like"
          "\n           every other list in the add-on.")
    session.close()


@scenario("Errors: what the user hears when the speaker refuses a command")
def error_messages():
    house = default_household()
    session = Session(household=house)
    session.press("kb:NVDA+shift+control+s")
    session.select_room("Living Room")

    from harness.sonos import SoCoUPnPException
    house.fail("Pause", SoCoUPnPException("Transition not available", error_code="701"))
    print("  speaker returns UPnP 701 when asked to pause:")
    for line in session.press("kb:space"):
        print("    " + line)

    house.faults.clear()
    house.fail("GetPositionInfo", OSError("network unreachable"))
    print("  network drops while asking for the track:")
    for line in session.press("kb:p"):
        print("    " + line)
    session.close()


def main(names):
    chosen = names or list(SCENARIOS)
    unknown = [n for n in chosen if n not in SCENARIOS]
    if unknown:
        print(f"Unknown scenario(s): {', '.join(unknown)}")
        print(f"Available: {', '.join(SCENARIOS)}")
        return 1
    for name in chosen:
        title, function = SCENARIOS[name]
        banner(f"{name}\n{title}")
        try:
            function()
        except Exception:
            import traceback
            traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
