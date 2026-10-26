# Sonos Controller

Local Sonos control for NVDA 2026.2, driven by a persistent keyboard layer.

- [English documentation](doc/en/readme.md)
- [Bulgarian documentation](doc/bg/readme.md)

The interface is English. Strings go through gettext, so a translation can be
added later without touching the code.

## Layout

| Path | What it holds |
|---|---|
| `globalPlugins/sonosController/` | the add-on itself |
| `globalPlugins/sonosController/_vendor/` | generated private dependency tree (SoCo and friends) |
| `doc/{en,bg}/` | user documentation, packaged with the add-on |
| `tests/` | unit tests and emulator-driven end-to-end tests |
| `tools/` | build, vendoring, and the NVDA emulator |
| `tools/harness/` | headless NVDA + simulated Sonos household ([readme](tools/harness/README.md)) |
| `sandbox/` | the same add-on in real NVDA, in a Windows Sandbox ([readme](sandbox/README.md)) |
| `research/` | hardware test notes, radio surveys, prototypes; not shipped |
| `build/` | generated packages and the pip staging area; not committed |

## Working on it

```bash
python -m unittest discover -s tests -v   # everything, no hardware needed
python tools/harness/scenarios.py         # walk through the behaviour as speech
python tools/build.py                     # build/sonosController-<version>.nvda-addon
```

`tools/build.py` takes the version from `manifest.ini`, so that file is the one
place a release number lives.

### Dependencies

Pinned in `requirements-sonos.txt` and installed outside the add-on, so NVDA
never loads them from a development checkout:

```bash
python -m pip install -r requirements-sonos.txt --target build/vendor-src
```

That staging area is only needed to re-pin a dependency. `python
tools/vendorize.py` regenerates the private `_vendor` tree from it
and `python tools/vendorize.py --check` audits it. The generated tree replaces
SoCo's two lxml uses with standard-library XML, so the package ships no native
extensions.

### Testing without speakers

`tools/harness` runs the real add-on against fake NVDA modules and a simulated
household, so a test can press keys and read back what NVDA would say. It also
counts speaker requests, which is how the responsiveness limits in
`tests/test_sonos_emulator.py` are set. `Session(network="real")` points the
same harness at speakers on the LAN.

What a fake wx cannot tell you is what a screen reader actually says, or
whether a control raises the events NVDA listens for. `sandbox/` answers that
in real NVDA: it builds the package, unpacks it into a Windows Sandbox, and
presses real keys at it ([readme](sandbox/README.md)).

```bash
.\sandbox\Run-Sandbox.ps1 -Scenario alarms
.\sandbox\Run-Sandbox.ps1 -Stop
```

## FFmpeg

The package ships no executable, which is what lets it be published: a binary
inside an add-on brings its licence obligations along, and the store has no
reason to trust one. `ffmpeg.py` instead looks for an FFmpeg the machine has
(configured path, then PATH) and, failing that, downloads the pinned official
Windows build into NVDA's configuration folder, checked against its SHA-256
before anything is unpacked.

Nothing is fetched until the user says yes. `relayhooks.py` raises
`NeedsFFmpeg` when a speaker refuses a stream that conversion would have
played, carrying the speaker's own complaint; the layer offers the download,
and on success replays the URL. Declining leaves playback as it was.

The pinned release is the one TikTok Live Reader installs, so a machine with
both add-ons fetches one archive. `tests/test_ffmpeg.py` serves a stand-in
archive from a local HTTP server, so no test touches the network; it covers a
good download, a tampered one, cancellation, and that neither failure leaves a
half-written FFmpeg behind. The relay's own tests run with the main suite.

## Installation for development

The junction `%APPDATA%\nvda\addons\sonosController` points at this project
root and its name matches `manifest.ini`. Restart NVDA after updating the
checkout, and do not install a second copy alongside it.
