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

@scenario("Alarm dialog: arrow keys run backwards on the volume slider")
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
        verdict = "INVERTED" if after_up < start else "ok"
        print(f"\n  verdict: arrows are {verdict} relative to what a user expects,")
        print("           and PageUp/PageDown disagree with the arrows.")
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
        print("  (the Days list has now appeared, with no announcement)\n")
        print(dialog.read())
        return False

    session.on_dialog(inspect)
    session.press("kb:enter")
    session.close()


@scenario("Group mode: Up arrow silently throws the selection away")
def group_up_cancels():
    session = Session()
    session.press("kb:NVDA+shift+control+s")
    session.select_room("Living Room")
    print("  " + "\n  ".join(session.press("kb:g")))
    print(f"  mode: {session.mode}")
    print("\n  selecting a second room with Right then Space:")
    print("  " + "\n  ".join(session.press("kb:rightArrow", "kb:space")))
    print("\n  now pressing Up, expecting it to move in the room list:")
    for line in session.press("kb:upArrow"):
        print("  " + line)
    print(f"  mode: {session.mode}")
    print("\n  verdict: the group selection was discarded and the key changed"
          "\n           the volume instead. Nothing said why.")
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
    print("\n  verdict: every arrow press re-reads the value over the network.")
    session.close()


@scenario("Local files: one network request per file in the folder")
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
    print(f"  25 files -> {session.count_calls()} network requests")
    print(f"  heard: {heard[-1] if heard else '(nothing)'}")
    print("\n  verdict: the count scales one-to-one with the file count, so the"
          "\n           documented 1000-file limit means ~1000 sequential requests.")
    session.close()


@scenario("Unload: when does terminate() stall NVDA?")
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
    for level, text in session.log.records:
        if level == "warning":
            print(f"    log warning: {text}")
    print("\n    -> this is the real stall: nothing interrupts an in-flight"
          "\n       request, so NVDA waits out the whole 4 s join timeout and"
          "\n       then leaks the thread.")
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
