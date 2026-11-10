# Sonos Controller - developer notes

An NVDA global plugin (NVDA 2026.2+, Python 3.13) that controls Sonos speakers on the local network from a
persistent keyboard layer. The user guide is `doc/en/readme.html`; it is written by hand and holds only what users
need. Everything about building and testing is here.

## Layout

| Path | What it holds |
|---|---|
| `globalPlugins/sonosController/` | the add-on itself |
| `globalPlugins/sonosController/_vendor/` | generated private dependency tree (SoCo and friends) |
| `doc/en/readme.html` | the user guide, packaged with the add-on |
| `tests/` | unit tests and emulator-driven end-to-end tests |
| `tools/` | build, vendoring, and the NVDA emulator |
| `tools/harness/` | headless NVDA + simulated Sonos household |
| `sandbox/` | the same add-on in real NVDA, in a Windows Sandbox |
| `build/` | generated packages and the pip staging area; not committed |

## Working on it

```bash
python -m unittest discover -s tests -v   # everything, no hardware needed
python tools/harness/scenarios.py         # walk through the behaviour as speech
python tools/build.py                     # build/sonosController-<version>.nvda-addon
ruff check globalPlugins tools sandbox    # ruff 0.14.5, settings in pyproject.toml
```

`tools/build.py` takes the version from `manifest.ini`, so that file is the one place a release number lives. The
build is deterministic and refuses a package that carries an executable or native extension.

For development, the junction `%APPDATA%\nvda\addons\sonosController` points at this project root and its name
matches `manifest.ini`. Restart NVDA after updating the checkout, and do not install a second copy alongside it.

## Conventions

- The interface is English. Strings go through gettext (`i18n.py`), so a translation can be added later without
  touching the code.
- Every message is one whole string with placeholders (`Group. {rooms}. Press Space to select, Enter to apply.`),
  never pieces joined with punctuation: the owner reviews and edits the list, and a translator needs whole
  sentences (the owner's poll answer, 10 November 2026). Text that goes inside a comma list (a room name, track
  details) has no full stop of its own. The owner's wording, edited on 10 November 2026, is the reference.
- Commit messages are one short line in the imperative, with no body and no trailer.
- The layer's keys are fixed; there is no NVDA settings panel.

## Settled behaviour

These came from use with a screen reader. Do not change them without asking.

- Normal wx controls (sliders, dropdowns), with their direction fixed: Up gives the larger value everywhere.
- Page Up and Page Down move 5 wherever Up and Down change a value, in the arrows' direction, the volume included.
  In the favorites and alarm lists they move five entries.
- The alarm's hour and minute wrap (23 -> 00), page keys with them; the duration fields stop at their ends.
- A new alarm repeats Once, and Once is first in the list.
- The alarm's volume slider has no 0, 100 or value labels (the owner's poll answer, 10 November 2026): they are
  loose static texts, which NVDA read with the dialog as "Volume 0 100". The slider says its value when reached.
- Grouping and transfer: Left and Right choose, Up and Down keep moving the volume.
- Shift+G breaks up the selected group at once, without asking: the playing room (the coordinator) keeps
  playing, the others stop and stand alone.
- F has two sections, Sonos favorites and Local favorites, switched with Left and Right; each keeps its place.
  Shift+F adds what the add-on is playing (a U address, a file or a folder) to Local favorites, and in the
  history the selected entry; it never adds to Sonos favorites. Delete asks before removing, in both sections of
  F and in the history alike (No is the default); F2 renames. A Sonos favorite renamed or removed here changes for
  the whole home, through SoCo's `favorites` (UpdateObject, DestroyObject), and the list is read again after.
  H lists what the add-on played, newest first, the last 50; a replay moves to the top and keeps a name given
  with F2 (the owner's polls, 10 November 2026; Sonos favorites editable by the poll of that evening).
- Speaker settings name the speaker once, when the selection reaches it, and never twice ("Move 2, Sonos Move 2").
- Long messages and the voice announcements stay.

## Dependencies

Pinned in `requirements-sonos.txt` and installed outside the add-on, so NVDA never loads them from a development
checkout:

```bash
python -m pip install -r requirements-sonos.txt --target build/vendor-src
```

That staging area is only needed to re-pin a dependency. `python tools/vendorize.py` regenerates the private
`_vendor` tree from it and `python tools/vendorize.py --check` audits it. The generated tree lives in the add-on's
private module namespace, so it never uses or replaces another add-on's top-level SoCo, Requests, urllib3 or ifaddr.
It replaces SoCo's two lxml uses with standard-library XML, so the package ships no native extensions, and its XML
diagnostics do not need `xml.dom`, which frozen NVDA lacks (`tools/check_soco_logging.py`).

Every change to the tree is made by `vendorize.py`; a file edited by hand is lost at the next regeneration. That
happened with the `xml.dom` guard in SoCo's `utils.prettify` when SoCo went to 0.32.2. SoCo prettifies every SOAP
body for a debug line whether or not anything logs it, so in NVDA each command failed and discovery said "local
control on port 1400 is unavailable". The emulator and the sandbox replace SoCo, so neither could see it. Two tests
in `tests/test_vendor_isolation.py` guard it now:

- One drives the vendored SoCo over HTTP against a local stand-in speaker with `xml.dom` missing.
- One checks every unguarded standard-library import against an installed NVDA's `library.zip`.

## FFmpeg

The package ships no executable: a binary inside an add-on brings its licence obligations along, and the store has
no reason to trust one. `ffmpeg.py` looks for an FFmpeg the machine has (the `ffmpegPath` setting, then PATH, then a
copy downloaded earlier) and, failing that, downloads the pinned official Windows build into NVDA's configuration
folder, checked against its SHA-256 before anything is unpacked. Only `ffmpeg.exe` and its licence are kept, and a
candidate must identify itself as FFmpeg before it is used.

Nothing is fetched until the user says yes. `relayhooks.py` raises `NeedsFFmpeg` when a speaker refuses a stream
that conversion would have played, carrying the speaker's own complaint; the layer offers the download, and on
success replays the URL. Declining leaves playback as it was. The speaker is always tried directly first; the relay
runs while playback lasts and stops when the source changes or the add-on unloads.

The pinned release is the one TikTok Live Reader installs, so a machine with both add-ons fetches one archive.
`tests/test_ffmpeg.py` serves a stand-in archive from a local HTTP server, so no test touches the network; it covers
a good download, a tampered one, cancellation, and that neither failure leaves a half-written FFmpeg behind.

## Playback and discovery

- URL playback inspects a bounded response, follows validated HTTP/HTTPS redirects, resolves M3U/PLS lists (MP3/AAC
  alternatives before Ogg) and keeps HLS manifests. HTML pages and authenticated URLs are rejected. HTTPS is never
  rewritten to HTTP.
- Some broadcast servers offer only a key exchange OpenSSL 3 dropped from its default list. Such a server is retried
  with a wider cipher list; the certificate and host name are still checked in full. Each network failure has its
  own wording (`urls.describe_network_error`).
- Success is announced only after the coordinator reports the selected URI and a repeated PLAYING state. A transient
  701 may retry Play without resetting the stream; buffering is allowed up to 12 seconds.
- Favorite playback separates source selection from Play and verifies the actual source after an ambiguous response.
- Local files are served by a small HTTP server bound to the interface that reaches the speaker: random URLs for the
  chosen files only, GET, HEAD and byte ranges, no directory listing. The add-on creates no firewall rules.
- Discovery saves up to 32 speaker IPv4 addresses in NVDA's configuration and probes them first on entry; SSDP runs
  only when none answers, and stops after the response window once one speaker has replied (four seconds if none
  does). Addresses are hints, never saved room or group state. A player's port comes from its SSDP LOCATION or the
  topology, so the further rooms of an Amp Multi, which share its address on other ports, are found too.
- The topology is shared, so `Controller.groups()` asks the selected room first and skips a room that does not
  answer: a portable gone to sleep must not stop the others being listed. A battery read that times out is
  reported as unread rather than failing the whole group.
- Ultra soundbars (Arc Ultra, Beam Ultra; SoCo's `is_ultra_soundbar`) get the speech-enhancement switch; the
  others the older dialog mode. A volume change a fixed-output Port or Connect refuses is named as fixed volume.
- Transfer and grouping reuse the rooms found on entry. Grouping waits for fresh topology after each change before
  confirming; commands are not repeated blindly, and partial changes are reported.
- Requests run in a bounded background queue. Pending commands are discarded when the layer turns off; a request
  already sent cannot be recalled.
- Volume is an integer 0-100; speakers truncate fractions. The add-on is disabled in secure mode.

What the stress test of 10 November 2026 found on the owner's speakers (an Era 100 pair, a Move 2, a Sonos
Play), each now covered by a unit test:

- A station the speaker plays reports its stream's file name as the title (`thevoicefm.m3u8`), its own
  placeholders while it connects (`ZPSTR_BUFFERING`), "Artist - Song" again after the song and the artist, and a
  station name sent in UTF-8 or Windows-1251 read as Latin-1. P names a station from GetMediaInfo
  (`media.describe`) and cleans the rest (`media.spoken`, `util.readable`); the U title is cleaned the same way.
- A radio takes two to seven seconds to start, and a room just given music is TRANSITIONING meanwhile. Space, G,
  Shift+G and T count that as playing (`util.PLAYING`), or Space said "Play" twice and T refused the room.
- A room just taken out of a group answered Play after more than three seconds, and played. A transport command
  that times out is checked against the state before it is called a failure (`Controller.confirm`). Any other
  timeout says "The speaker did not answer. Try again."; timeouts and Sonos error codes are logged at debug level,
  not as tracebacks.
- A URL typed as a browser shows it (Cyrillic path, IDN host) reaches the speaker percent-encoded and in IDNA; a
  404 or 500 has its own message; Opus served as `audio/ogg` goes to FFmpeg at once instead of after twelve seconds.
- A Move 2 just switched on reports battery level 0 from `UNKNOWN_powerSource`: read as unread, not as empty.
- Sonos refuses a second alarm with the same room, time and repeat with error 801; it is said in words. The alarm
  list says the repeat in the dialog's words, not `ON_135`. S with a timer running opens on what is left of it.
- One file played with Control+F is named ("Playing 01-silence"), not counted ("Playing 1 files."). F2 keeps a
  name to 160 characters, like a station's name.
- The scripts and the fuzzer that found these live outside the repository; they drive `Session(network="real")`
  with a watchdog that puts any playing speaker above 2 percent back to 1.

## Local favorites and history

`library.py` keeps both lists in `sonosController-library.json` in NVDA's configuration folder
(`NVDAState.WritePaths.configDir`), beside the downloaded FFmpeg, so they survive updates and follow a portable
copy. It is written to a temporary file and moved into place; a file that cannot be read is renamed `.json.bad`
rather than overwritten, and entries that do not validate are skipped. Entries are plain dicts of three kinds:
`url`, `files` (path and `recursive`) and `sonos` (a Sonos favorite's URI, history only, looked up again in the
household when replayed).

Every play the add-on starts (`urlPlay`, `localPlay`, Enter in F or H) goes through `_playEntry` or `_record`, which
puts the entry at the top of the history with `media`, the URI the speaker reports once playing. Shift+F in the
layer looks that URI up, so it adds only what the add-on started, even after NVDA restarts. URL playback returns a
`util.Played`, a str that also carries the stream's name (`name`, since `title` is a str method), taken from
`icy-name` or the playlist; that is the name a history entry gets.

`menus.EntryList` is the shared list behaviour (move, remove, rename); `Favorites` adds the Sonos section and
`History` is the plain list. The worker asks on the main thread (`_confirmRemove`, `_askRename`) and acts on the
answer as `menuDeleteConfirmed` / `menuRenamed`, by entry key rather than position.

## The emulator

`tools/harness` loads the real, unmodified `globalPlugins/sonosController` against fake NVDA modules and a
simulated household, so a test can press keys and read back what NVDA would say. It needs only the standard
library.

```
tools/harness/
  session.py           the emulator: boots the add-on, presses keys, collects speech
  nvda.py              fake config / ui / inputCore / scriptHandler / logHandler
  fakewx.py            fake wx that models Win32 control keyboard behaviour
  sonos.py             simulated speakers, groups, favorites, alarms, transport; product_household()
  scenarios.py         runnable walk-throughs of the behaviour
```

```python
from harness import Session

session = Session()                              # 4 rooms, 2 favorites, 1 alarm
session.press("kb:NVDA+shift+control+s")         # toggle the layer
session.select_room("Living Room")
print(session.press("kb:p"))                     # -> ['Sonos queue, Blue Monday, ...']
session.close()
```

`press()` reproduces NVDA's dispatch order: every `decide_executeGesture` handler runs first, and only then is the
gesture looked up in the add-on's bindings. That is what makes mode-switching bugs visible, because the add-on
unbinds keys from inside its own handler. After each press the emulator waits until the worker thread, the
simulated network and the `wx.CallAfter` queue are idle, so assertions do not race.

Modal dialogs are driven from the test through `session.on_dialog(callback)`; the dialog's `read()` gives the title
and every visible control in tab order, `focus_on()` and `press()` use real Win32 key semantics per control, and the
callback returns True for the default button or False to cancel. `wx.MessageBox`, `wx.TextEntryDialog` and file
pickers are answered through `session.responder`.

Every operation that would cross the network goes through `Household.call`, so round trips can be counted
(`session.count_calls()`) and given a latency (`default_household(latency=0.05)`); that is how the responsiveness
limits in `tests/test_sonos_emulator.py` are set. `house.fail(...)` injects a SoCo error.

`MODELS` in `sonos.py` lists the products the add-on is meant for. Like SoCo, a simulated speaker decides from its
model name whether it is a soundbar or an Ultra soundbar, and from its bonded speakers whether it has surrounds or
a subwoofer: `house.home_theater(...)`, `house.stereo_pair(...)` and `house.multi_zone(...)` (an Amp Multi: one
address, a port per room) build them, with the bonded speakers invisible. Reading a setting a model lacks raises
`AttributeError`, or the UPnP error the model gives (a Port asked about touch controls). `player.online = False`
makes one speaker time out and only that one. `product_household()` has one of every kind, and
`tests/test_sonos_products.py` walks it; its `SoCoDrift` tests fail when a name the add-on or the simulator uses is
not the vendored SoCo's, which is what keeps a SoCo update honest.

Each `Session` gets its own NVDA configuration folder (`session.config_dir`, a fake `NVDAState`), removed on
`close()`, so the library file a test writes never outlives it; `tests/test_sonos_library.py` reads it back.

`Session(network="real")` keeps the vendored SoCo, so the add-on performs genuine discovery and control on the LAN.
Use it only against speakers you are willing to have change state.

Limits:

- SSDP is stubbed in `sim` mode; the saved-address reconnect path runs the real `discovery.py`.
- `fakewx` models focus, visibility, tab order and key handling, not layout or what a screen reader says.
- Capability lookups (`is_soundbar`, `has_subwoofer`, ...) count as requests, because SoCo asks the device.
- The `Alarms` registry in `sonos.py` is a singleton, like SoCo's: call `session.alarms_module.reset()` when stale
  alarm state matters. `fakewx.Dialog.modal_hook` is process-global, so one `Session` at a time.

## Real NVDA in Windows Sandbox

The emulator cannot tell what a screen reader actually says, whether a wx control raises the events NVDA listens
for, or whether a dialog is usable with the keyboard. `sandbox/` answers that with a portable NVDA, the built
add-on and real key presses.

```bash
.\sandbox\Run-Sandbox.ps1 -Scenario smoke
.\sandbox\Run-Sandbox.ps1 -Scenario alarms -Nvda 2026.3
.\sandbox\Run-Sandbox.ps1 -Scenario norooms -Household empty
.\sandbox\Run-Sandbox.ps1 -Scenario products -Household products
.\sandbox\Run-Sandbox.ps1 -Stop
```

The first run starts the sandbox and leaves it open; later runs reuse it. It starts minimised, without taking the
focus, and the agent mutes every playback device of the sandbox before each run; a run whose devices cannot all be
muted does not start. Windows Sandbox runs one instance at a time, so `-Stop` when finished.

`Run-Sandbox.ps1` builds the package with `tools/build.py` and unpacks it into the stage, so a run exercises exactly
what would be published. `artifacts\sandbox` is mapped read-only as `C:\Stage` and `artifacts\sandbox-runs` writable
as `C:\Runs`; the agent writes a heartbeat there and takes one request at a time. Per run, `SandboxAgent.ps1` makes
a portable NVDA of the requested version once and starts it with a fresh configuration (silent synthesizer, no
welcome or update dialogs, log level 12, the staged add-on). `driver.py` presses keys with SendInput, reads the
speech from NVDA's log and writes `results.txt`: one PASS/FAIL line per check, then the transcript. Each call
removes the earlier run folders. Changing `SandboxAgent.ps1` needs `-Stop` first: the running agent keeps its copy.

The sandbox's network sits behind its own virtual switch, so SSDP never reaches real speakers. The agent copies
`sandbox/household.py` and `tools/harness/sonos.py` into the staged add-on and appends four lines to the staged
`vendors.py` and `discovery.py` that use them when `SONOS_SIM` is set. The repository is never modified, and
`tests/test_sandbox_staging.py` does the same staging on the host so the hook is checked by the suite.

Scenarios:

- `smoke` - the layer turns on and names a room, the arrows move the volume, play and track reporting answer.
- `pagekeys` - Page Up and Page Down move five, in the arrows' direction, for the volume and in speaker settings.
- `alarms` - editing and adding an alarm say nothing about the volume on opening, Up gives the next larger hour,
  the hours wrap, a new alarm repeats Once, a custom duration stops at its largest hour.
- `settings` - the settings sweep, and the room named once rather than twice.
- `norooms` - an empty household is reported instead of hanging.
- `groups` - G groups two rooms, Shift+G breaks them up and then says the room is not grouped, T moves the music.
- `products` - with `-Household products`: all 22 rooms answer, the Arc Ultra on TV and its speech enhancement, a
  Move 2's battery, a Port at fixed output.
- `library` - F's two sections, a Sonos favorite renamed and removed, another played and found in H, F2 renaming
  it in NVDA's own reading of the dialog, and Delete asking first (Enter keeps, Y removes).

Every scenario also fails if NVDA logged an error or left a crash dump; the agent keeps the log written while NVDA
closes as `nvda-closing.log`, since a restarted NVDA writes its log anew.

Staged by hand: `artifacts\sandbox\nvda\nvda_<version>.exe`, one installer per NVDA version, and the portable Python
for the driver in `.tools\python313`.
