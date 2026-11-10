"""Drives NVDA with real key presses inside the sandbox and checks what it says.

    python driver.py <scenario> <nvda log> <results folder>

Runs only inside Windows Sandbox, where SandboxAgent.ps1 has already started
NVDA with the add-on installed and the simulated household behind it: this
presses keys on the sandbox's own desktop.  Speech is read back from NVDA's log
("Speaking [...]" lines at log level 12), so what a check asserts is what a
user would have heard, not what the add-on believes it said.

Writes results.txt - one PASS/FAIL line per check, then the whole transcript -
and exits 1 if any check failed.

Adapted from the TikTok Live Reader add-on's sandbox/driver.py.
"""

import ast
import ctypes
import re
import sys
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
EXTENDED = {"insert", "home", "end", "up", "down", "left", "right", "delete",
            "pageup", "pagedown"}
VK = {
    "nvda": 0x2D, "insert": 0x2D,
    "control": 0x11, "shift": 0x10, "alt": 0x12,
    "home": 0x24, "end": 0x23,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "pageup": 0x21, "pagedown": 0x22,
    "tab": 0x09, "enter": 0x0D, "escape": 0x1B, "space": 0x20, "delete": 0x2E, "f2": 0x71,
}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(name, up):
    vk = VK.get(name) or ord(name.upper())
    flags = (KEYEVENTF_KEYUP if up else 0) | (
        KEYEVENTF_EXTENDEDKEY if name in EXTENDED or name == "nvda" else 0)
    scan = user32.MapVirtualKeyW(vk, 0)
    event = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, flags, 0, 0))
    user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


def press(combo):
    """Press a combination such as "nvda+shift+control+s"."""
    keys = combo.lower().split("+")
    for key in keys:
        _send(key, up=False)
        time.sleep(0.03)
    for key in reversed(keys):
        _send(key, up=True)
        time.sleep(0.03)


def type_text(text):
    """Type letters and spaces, as a user would into an edit field."""
    for character in text:
        if character == " ":
            press("space")
        elif character.isupper():
            press("shift+" + character.lower())
        else:
            press(character)


STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
# Speech commands in the log, e.g. "LangChangeCommand ('en')".
COMMAND = re.compile(r"\b[A-Z]\w*\s?\((?:[^()'\"]|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")*\)")
# A string or a command, whichever starts first: a spoken "Volume (%)" is a
# string with brackets, not a command.
TOKEN = re.compile(f"(?P<string>{STRING.pattern})|{COMMAND.pattern}")


class NvdaLog:
    def __init__(self, path):
        self.path = Path(path)

    def text(self):
        try:
            return self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def speech(self):
        """Everything spoken so far, one string per utterance."""
        spoken = []
        for line in self.text().splitlines():
            if line.startswith("Speaking ["):
                parts = [ast.literal_eval(match.group("string"))
                         for match in TOKEN.finditer(line[len("Speaking ["):])
                         if match.group("string")]
                parts = [part for part in parts if part.strip()]
                if parts:
                    spoken.append(" ".join(parts))
        return spoken

    def errors(self):
        lines, block = [], []
        for line in self.text().splitlines():
            if re.match(r"^(ERROR|CRITICAL) - ", line):
                block = [line]
                lines.append(block)
            elif block and line and not re.match(r"^[A-Z]+ - ", line):
                block.append(line)
            else:
                block = []
        return ["\n".join(item) for item in lines]


class Run:
    def __init__(self, log, out):
        self.log = NvdaLog(log)
        self.out = Path(out)
        self.results = []
        self.mark = 0

    def check(self, name, ok, detail=""):
        self.results.append(("PASS" if ok else "FAIL", name, detail))
        return ok

    def since_mark(self):
        return self.log.speech()[self.mark:]

    def press_and_hear(self, combo, pattern, name, timeout=8):
        """Press keys and wait for an utterance after the press to match."""
        self.mark = len(self.log.speech())
        started = time.monotonic()
        press(combo)
        while time.monotonic() - started < timeout:
            for utterance in self.since_mark():
                if re.search(pattern, utterance):
                    self.check(name, True,
                               f"{utterance!r} after {time.monotonic() - started:.2f} s")
                    return utterance
            time.sleep(0.05)
        self.check(name, False, f"heard {self.since_mark()!r}")
        return None

    def hear(self, pattern, name, timeout=20):
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            for utterance in self.since_mark():
                if re.search(pattern, utterance):
                    self.check(name, True, repr(utterance))
                    return utterance
            time.sleep(0.1)
        self.check(name, False, f"heard {self.since_mark()!r}")
        return None

    def number(self, combo, name, timeout=8):
        """Press keys and return the first number in what was said."""
        heard = self.press_and_hear(combo, r"\d", name, timeout)
        if heard is None:
            return None
        return int(re.search(r"\d+", heard).group())

    def wait_for_nvda(self, timeout=90):
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if "NVDA initialized" in self.log.text():
                time.sleep(3)
                return self.check("NVDA started", True,
                                  f"{time.monotonic() - started:.1f} s")
            time.sleep(0.5)
        return self.check("NVDA started", False)

    def finish(self):
        errors = self.log.errors()
        self.check("no errors in NVDA's log", not errors, "\n".join(errors)[:4000])
        lines = [f"{status}  {name}" + (f"  - {detail}" if detail else "")
                 for status, name, detail in self.results]
        lines += ["", "--- transcript ---"] + self.log.speech()
        (self.out / "results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0 if all(status == "PASS" for status, _, _ in self.results) else 1


TOGGLE = "nvda+shift+control+s"

SW_RESTORE = 9


def raise_window(title, timeout=5):
    """Wait for a window and give it the keyboard.

    A dialog the add-on opens belongs to NVDA's hidden main frame, and a
    portable NVDA has no UIAccess, so Windows will not let it take the
    foreground from the desktop: the dialog opens behind it and the keys go to
    the desktop instead.  A user sees the dialog and clicks or alt-tabs to it;
    the driver has to do the same thing deliberately.  This is a property of
    running NVDA portable, not of the add-on.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        handle = user32.FindWindowW(None, title)
        if handle:
            user32.ShowWindow(handle, SW_RESTORE)
            user32.SwitchToThisWindow(handle, True)
            user32.SetForegroundWindow(handle)
            time.sleep(0.6)
            return handle
        time.sleep(0.1)
    return None


def open_dialog(run, combo, title, pattern, name):
    """Press the key that opens a dialog, then make sure it has the keyboard."""
    run.mark = len(run.log.speech())
    press(combo)
    if raise_window(title) is None:
        return run.check(name, False, f"no window titled {title!r} appeared")
    return run.hear(pattern, name, timeout=6)


def quiet_opening(run, name):
    """Only the title and the focused field are said as the alarm dialog opens."""
    time.sleep(1)
    said = run.since_mark()
    run.check(name, not any("Volume" in u or re.search(r"\b0\b.*\b100\b", u) for u in said), repr(said))


def tab_to(run, pattern, name, tries=8):
    """Tab through a dialog until a field matching ``pattern`` is announced."""
    for _attempt in range(tries):
        found = run.press_and_hear("tab", pattern, name, timeout=2)
        if found:
            return found
        run.results.pop()
    run.check(name, False)
    return None


def enter_layer(run):
    """Turn the layer on and wait for the room it reports."""
    run.press_and_hear(TOGGLE, r"^Connecting\.\.\.$", "layer announces it is connecting")
    return run.hear(r"Sonos", "the selected room is announced", timeout=20)


def scenario_smoke(run):
    """The add-on loads in real NVDA, controls a room and switches off again."""
    if not run.wait_for_nvda():
        return
    enter_layer(run)
    up = run.number("up", "volume up is spoken as a percentage")
    down = run.number("down", "volume down is spoken as a percentage")
    if up is not None and down is not None:
        run.check("the arrows move the volume one step", down == up - 1,
                  f"up said {up}, down said {down}")
    run.press_and_hear("space", r".", "play or pause answers")
    run.press_and_hear("p", r".", "the track can be reported")
    # An unrelated key leaves the layer and reaches Windows, which is what
    # makes the layer safe to leave on.
    run.press_and_hear(TOGGLE, r".", "the layer toggles off")


def scenario_pagekeys(run):
    """Page Up and Page Down move five, the way the arrows move one."""
    if not run.wait_for_nvda():
        return
    enter_layer(run)
    start = run.number("up", "volume is readable")
    if start is None:
        return
    paged = run.number("pageup", "Page Up is spoken")
    if paged is not None:
        run.check("Page Up raises the volume by five", paged == start + 5,
                  f"{start} then {paged}")
    back = run.number("pagedown", "Page Down is spoken")
    if back is not None:
        run.check("Page Down lowers it by five", back == start, f"{paged} then {back}")

    # Inside the settings sweep the same keys move the setting's value, not the
    # volume: Right raises Bass by one, so Page Up must raise it by five.
    run.press_and_hear("control+s", r"Settings", "speaker settings open")
    run.press_and_hear("right", r"Bass, 1", "Right raises Bass by one")
    bass = run.number("pageup", "Page Up moves Bass")
    if bass is not None:
        run.check("Bass pages by five", bass == 6, f"Bass went to {bass}")
    run.press_and_hear("escape", r".", "Escape leaves the settings")


def scenario_alarms(run):
    """The alarm dialog in real NVDA: the arrows, the wrap, the default and the duration."""
    if not run.wait_for_nvda():
        return
    enter_layer(run)
    run.press_and_hear("a", r"Alarms", "the alarms menu opens")
    # The slider's 0, 100 and value used to be loose static texts, read with the
    # dialog as "Volume 0 100"; the owner heard it on editing an alarm.
    if open_dialog(run, "enter", "Sonos alarm", r"Hour", "Enter edits the existing alarm"):
        quiet_opening(run, "editing an alarm says nothing about the volume on opening")
        run.press_and_hear("escape", r".", "the edit dialog closes")
    if not open_dialog(run, "n", "Sonos alarm", r"Hour",
                       "the new alarm dialog opens on the hour"):
        return
    quiet_opening(run, "a new alarm says nothing about the volume on opening")

    # The defect this scenario exists for: Up must give the next larger hour.
    first = run.number("home", "Home gives the smallest hour")
    second = run.number("up", "Up is spoken")
    if first is not None and second is not None:
        run.check("Up takes the next larger hour", second == first + 1,
                  f"from {first} to {second}")
    run.press_and_hear("home", r"\d", "back to the smallest hour")
    wrapped = run.number("down", "Down from the smallest hour is spoken")
    if wrapped is not None:
        run.check("the hours wrap round past the smallest", wrapped == 23,
                  f"Down from 00 said {wrapped}")

    # Repeat must offer the one-off alarm first, so a mistake cannot keep
    # going off unnoticed.
    found = tab_to(run, r"Repeat", "Repeat is reachable")
    if found:
        run.check("a new alarm repeats Once by default", "Once" in found, repr(found))

    # A duration is a span, not a clock: Up on its largest hour stays there, so
    # the Down after it says 22.  Had it wrapped to 00, Down would say 23.
    if tab_to(run, r"Duration", "Duration is reachable"):
        run.press_and_hear("down", r"Custom", "a custom duration can be chosen")
        if run.press_and_hear("tab", r"Duration hour", "the duration hour follows"):
            top = run.number("end", "End gives the largest duration hour")
            press("up")
            time.sleep(1)
            below = run.number("down", "Down from there is spoken")
            if top is not None and below is not None:
                run.check("the duration stops at its largest hour", (top, below) == (23, 22),
                          f"End said {top}, Up then Down said {below}")
    run.press_and_hear("escape", r".", "the dialog closes")


def scenario_settings(run):
    """The settings sweep, and the room named once rather than twice."""
    if not run.wait_for_nvda():
        return
    enter_layer(run)
    heard = run.press_and_hear("control+s", r"Settings", "settings open")
    if heard:
        # "Move 2, Sonos Move 2" was the defect: the model is only added when
        # the room name does not already carry it.
        run.check("the room is not named twice",
                  not re.search(r"(\b[\w ]+\b).*Sonos \1", heard), repr(heard))
    # Down and Up walk the list of settings; Left and Right change the value of
    # the one you are on.
    for name in ("Treble", "Loudness", "Status light", "Touch controls"):
        if not run.press_and_hear("down", name, f"{name} is next in the list", timeout=4):
            break
    run.press_and_hear("up", r"Status light", "Up walks back up the list")
    # A switch is already at one end, so Left turns it off and Right back on.
    run.press_and_hear("left", r"Status light, off", "Left turns a switch off")
    run.press_and_hear("right", r"Status light, on", "Right turns it back on")
    run.press_and_hear("escape", r".", "Escape leaves the settings")


def scenario_norooms(run):
    """With no speakers at all the add-on says so instead of hanging."""
    if not run.wait_for_nvda():
        return
    run.press_and_hear(TOGGLE, r"No Sonos rooms found\.", "an empty household is reported",
                       timeout=30)


def scenario_groups(run):
    """Grouping, breaking up with Shift+G, and moving the music on."""
    if not run.wait_for_nvda():
        return
    heard = enter_layer(run)
    if not heard:
        return
    # The layer opens on Kitchen, the first room by name; Living Room is the one playing.
    if "Living Room" not in heard:
        run.press_and_hear("right", r"^Living Room, ", "Right reaches the playing Living Room")
    run.press_and_hear("g", r"^Group\. Living Room", "G starts grouping from the playing room")
    run.press_and_hear("right", r"selected", "Right reaches the next room")
    run.press_and_hear("space", r", selected", "Space selects it")
    run.press_and_hear("enter", r"^Playing rooms: ", "Enter groups the rooms")
    run.press_and_hear("shift+g", r"^Rooms ungrouped\. Living Room keeps playing\.$",
                       "Shift+G breaks the group up and names the room that plays on")
    run.press_and_hear("shift+g", r"^Living Room is not grouped\.$",
                       "Shift+G on a room alone says so")
    run.press_and_hear("t", r"^Transfer from Living Room to ", "T offers a free room")
    run.press_and_hear("space", r"^Audio transferred to ", "Space moves the music")
    run.press_and_hear(TOGGLE, r".", "the layer toggles off")


def scenario_products(run):
    """One of every kind of Sonos product, room by room (household "products")."""
    if not run.wait_for_nvda():
        return
    if not enter_layer(run):
        return
    rooms = []
    for _ in range(30):
        heard = run.press_and_hear("right", r"percent|Fixed volume", "the next room answers", timeout=6)
        if heard is None:
            return
        room = heard.split(",")[0]
        if room in rooms:
            break
        rooms.append(room)
    run.check("every room is listed, the sleeping Roam SL is not",
              len(rooms) == 22 and "Garage" not in rooms, repr(rooms))
    # Walk on to particular rooms with Right, as a user would.
    def go(name):
        for _ in range(len(rooms) + 1):
            heard = run.press_and_hear("right", r".", f"Right towards {name}", timeout=4)
            if heard is None:
                return False
            if heard.startswith(name + ","):
                return True
        return run.check(f"{name} is reached", False)
    if go("Living Room"):
        run.press_and_hear("p", r"^TV, playing$", "an Arc Ultra on TV says so")
        run.press_and_hear("control+s", r"^Settings\. Living Room, Sonos Arc Ultra, ",
                           "the Arc Ultra's settings name it")
        found = False
        for _ in range(12):
            heard = run.press_and_hear("down", r".", "walking the Arc Ultra's settings", timeout=4)
            if heard and heard.startswith("Speech enhancement"):
                found = True
                break
        run.check("the Arc Ultra offers speech enhancement", found)
        run.press_and_hear("escape", r".", "Escape leaves the settings")
    if go("Patio"):
        run.press_and_hear("b", r"^Patio, battery \d+ percent", "a Move 2 reports its battery")
    if go("Hi-Fi"):
        run.press_and_hear("up", r"^Fixed volume on Hi-Fi\.$", "a Port at fixed output says so")
    run.press_and_hear(TOGGLE, r".", "the layer toggles off")


def scenario_library(run):
    """F's two sections, the history, and NVDA reading the rename and remove dialogs."""
    if not run.wait_for_nvda():
        return
    if not enter_layer(run):
        return
    run.press_and_hear("f", r"^Sonos favorites\. Test Radio, 1 of 2$", "F opens on the Sonos favorites")
    run.press_and_hear("right", r"^No local favorites\. Shift\+F adds what is playing\.$",
                       "Right shows the empty Local favorites")
    run.press_and_hear("left", r"^Sonos favorites\. Test Radio, 1 of 2$", "Left goes back")
    run.press_and_hear("delete", r"^Sonos favorites are changed in the Sonos app\.$",
                       "a Sonos favorite is not removed from here")
    run.press_and_hear("enter", r"^(Playing|Starting) Test Radio", "Enter plays it")
    run.press_and_hear("h", r"^History\. Test Radio, 1 of 1$", "H lists what was played")
    run.press_and_hear("shift+f", r"^Test Radio is already a Sonos favorite\.$",
                       "Shift+F knows a Sonos favorite")
    if open_dialog(run, "f2", "Rename", r"Name", "F2 opens the rename dialog"):
        run.check("the dialog reads the current name", any("Test Radio" in u for u in run.since_mark()),
                  repr(run.since_mark()))
        press("control+a")
        type_text("Morning")
        run.press_and_hear("enter", r"^Renamed\. Morning, 1 of 1$", "the new name is said")
    if open_dialog(run, "delete", "Remove", r"Remove Morning from the history\?", "Delete asks first"):
        # No is the default, so Enter keeps the entry.
        press("enter")
        time.sleep(1)
        run.press_and_hear("down", r"^Morning, 1 of 1$", "Enter on the question keeps the entry")
    if open_dialog(run, "delete", "Remove", r"Remove Morning", "Delete asks again"):
        run.press_and_hear("y", r"^Removed\. History is empty\.$", "Yes removes it")
    run.press_and_hear("escape", r"^Sonos$", "Escape leaves the history")
    run.press_and_hear(TOGGLE, r".", "the layer toggles off")


SCENARIOS = {
    "smoke": scenario_smoke,
    "pagekeys": scenario_pagekeys,
    "alarms": scenario_alarms,
    "settings": scenario_settings,
    "norooms": scenario_norooms,
    "groups": scenario_groups,
    "products": scenario_products,
    "library": scenario_library,
}


def main():
    scenario, log, out = sys.argv[1:4]
    run = Run(log, out)
    try:
        SCENARIOS[scenario](run)
    except Exception as error:
        run.check("the scenario ran to the end", False, repr(error))
    sys.exit(run.finish())


if __name__ == "__main__":
    main()
