"""Find an FFmpeg the user already has.

The public package cannot bundle FFmpeg: the executable is large, and shipping
it would drag its licence obligations into an NVDA add-on.  So the add-on looks
for one on the machine instead.  When it finds a usable FFmpeg the URL player
gains a fallback that can remux or transcode a stream the speaker refuses;
when it does not, playback behaves exactly as it did before.

Search order, first hit wins:

1. The path set in NVDA's configuration, if the user pointed at one.
2. ``ffmpeg`` on ``PATH``.
3. The usual per-user and machine-wide install locations.
4. A copy bundled next to the add-on, which only the personal build has.
"""

import os
import platform
import shutil
import subprocess
from pathlib import Path

from .i18n import _

#: How long to wait for ``ffmpeg -version`` before giving up on a candidate.
PROBE_TIMEOUT = 5

#: Encoders the fallback needs; without libmp3lame it can only remux.
REQUIRED_ENCODER = "libmp3lame"


def _windows_locations():
    """Where the common installers put ffmpeg.exe."""
    roots = []
    for variable in ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    home = Path.home()
    candidates = []
    for root in roots:
        candidates += [
            root / "ffmpeg" / "bin" / "ffmpeg.exe",
            # winget keeps a versioned folder under Links or Packages.
            root / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
        ]
    candidates += [
        home / "scoop" / "shims" / "ffmpeg.exe",
        home / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe",
        Path("C:/ProgramData/chocolatey/bin/ffmpeg.exe"),
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
    ]
    return candidates


def bundled_path():
    """A copy shipped inside the add-on, used only by the personal build."""
    architecture = platform.machine().lower()
    folder = ("arm64" if architecture in ("arm64", "aarch64")
              else "x64" if architecture in ("amd64", "x86_64") else "")
    if not folder:
        return None
    return Path(__file__).resolve().parent / "bin" / folder / "ffmpeg.exe"


def candidates(configured=""):
    """Every place worth looking, in order of preference."""
    found = []
    if configured:
        found.append(Path(configured))
    on_path = shutil.which("ffmpeg")
    if on_path:
        found.append(Path(on_path))
    if os.name == "nt":
        found += _windows_locations()
    else:  # pragma: no cover - the add-on only runs on Windows
        found += [Path("/usr/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg")]
    bundled = bundled_path()
    if bundled is not None:
        found.append(bundled)
    unique = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def describe(executable, run=None):
    """Return ``(version, encoders)`` for a candidate, or ``None`` if unusable.

    The executable is asked to identify itself rather than trusted on its
    name, so a stray file called ffmpeg.exe cannot be launched as a decoder.
    """
    run = run or subprocess.run
    # Normalise first: a relative path with forward slashes does not always
    # start on Windows, and candidates can arrive as plain strings.
    executable = Path(executable)
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = run([str(executable), "-hide_banner", "-version"],
                     capture_output=True, text=True, timeout=PROBE_TIMEOUT,
                     creationflags=creationflags)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or "") + (result.stderr or "")
    if "ffmpeg version" not in output.lower():
        return None
    version = output.split("\n", 1)[0].strip()
    # The build line lists the options it was compiled with.
    encoders = {name for name in (REQUIRED_ENCODER, "aac", "libopus") if "--enable-" + name in output}
    if "aac" not in encoders:
        encoders.add("aac")  # the native AAC encoder is always present
    return version, encoders


def find(configured="", run=None):
    """Locate a usable FFmpeg.

    Returns ``(path, version, encoders)``, or ``(None, reason, set())`` with a
    message suitable for speaking when nothing usable was found.
    """
    seen = candidates(configured)
    if configured and not Path(configured).exists():
        return None, _("The configured FFmpeg path does not exist."), set()
    for path in seen:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        details = describe(path, run)
        if details is not None:
            version, encoders = details
            return path, version, encoders
    return None, _("No FFmpeg was found on this computer."), set()
