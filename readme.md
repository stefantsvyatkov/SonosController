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
| `research/` | hardware test notes, radio surveys, prototypes; not shipped |
| `experimental/ffmpeg-relay/` | the same add-on with an FFmpeg bundled in; not shipped |
| `build/` | generated packages and the pip staging area; not committed |
| `site_scons/`, `sconstruct`, `templateDocs/` | retained upstream add-on template; unused by this build |

## Working on it

```bash
python -m unittest discover -s tests -v   # everything, no hardware needed
python tools/harness/scenarios.py         # walk through the behaviour as speech
python tools/build.py                     # build/sonosController-<version>.nvda-addon
```

`tools/build.py` takes the version from `manifest.ini`, so that file is the one
place a release number lives.

### Dependencies

Pinned in `requirements-sonos.txt` and installed into `build/vendor-src/`,
which is outside the add-on so NVDA never loads them from a development
checkout. `python tools/vendorize.py` regenerates the private `_vendor` tree
and `python tools/vendorize.py --check` audits it. The generated tree replaces
SoCo's two lxml uses with standard-library XML, so the package ships no native
extensions.

### Testing without speakers

`tools/harness` runs the real add-on against fake NVDA modules and a simulated
household, so a test can press keys and read back what NVDA would say. It also
counts speaker requests, which is how the responsiveness limits in
`tests/test_sonos_emulator.py` are set. `Session(network="real")` points the
same harness at speakers on the LAN.

## FFmpeg

The public package converts streams a speaker refuses whenever the machine
already has FFmpeg. `ffmpeg.py` finds it (configured path, PATH, the usual
installer locations) and `relayhooks.py` decides whether to use it; with no
FFmpeg present the add-on plays URLs directly, as it always did. Nothing is
bundled and nothing is downloaded, so the package stays free of executables.

`experimental/ffmpeg-relay/` builds a personal variant for a machine without
FFmpeg installed:

```bash
python experimental/ffmpeg-relay/tools/build.py
```

That package is the public source byte-for-byte plus one added file, the
FFmpeg executable, which `ffmpeg.bundled_path()` picks up. Because the code
does not differ, refactoring cannot silently break the variant.

The relay's own tests run with the main suite. Two further tests cover the
personal build itself and need the executable present, so they stay separate:

```bash
python -m unittest discover -s experimental/ffmpeg-relay/tests
```

## Installation for development

The junction `%APPDATA%\nvda\addons\sonosController` points at this project
root and its name matches `manifest.ini`. Restart NVDA after updating the
checkout, and do not install a second copy alongside it.
