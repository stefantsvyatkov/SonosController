"""The optional FFmpeg: found if the machine has one, downloaded if not.

The add-on ships no executable.  That is what lets it be published: a binary
inside the package would bring its licence obligations with it, and the store
has no reason to trust an executable it cannot see the source of.  Instead the
add-on uses an FFmpeg the machine already has, or offers to fetch the official
Windows build once, on the first stream a speaker refuses.

A download is kept in NVDA's configuration folder, so updating the add-on does
not throw it away, and it is checked against its SHA-256 before anything is
unpacked.  Nothing is fetched until the user says yes.

Where it looks, first hit wins:

1. The path set in NVDA's configuration, if the user pointed at one.
2. ``ffmpeg`` on ``PATH``: an FFmpeg already installed saves the download.
3. The copy this module downloaded.
"""

import hashlib
import shutil
import ssl
import subprocess
import urllib.request
import zipfile
from pathlib import Path

from .i18n import _

#: How long to wait for ``ffmpeg -version`` before giving up on a candidate.
PROBE_TIMEOUT = 5

#: Encoders the fallback needs; without libmp3lame it can only remux.
REQUIRED_ENCODER = "libmp3lame"

#: The official Windows build, pinned by version and checked by digest.  The
#: same release the TikTok Live Reader add-on installs, so a machine with both
#: fetches one archive.
URL = "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"
SIZE = 114_768_076
SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
#: What is kept from the archive, by its name in the FFmpeg folder.  ffprobe is
#: not needed: the relay asks FFmpeg itself what a stream contains.
MEMBERS = {
    "ffmpeg.exe": "ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe",
    "LICENSE.txt": "ffmpeg-9.0.2-essentials_build/LICENSE",
}
CHUNK = 256 * 1024


class Cancelled(Exception):
    """Raised by :func:`install` when ``cancelled()`` asks it to stop."""


def folder():
    """Where a downloaded FFmpeg lives: NVDA's configuration folder."""
    from NVDAState import WritePaths

    return Path(WritePaths.configDir) / "sonosController-ffmpeg"


def downloaded():
    """The downloaded FFmpeg's path when it is there, else None."""
    try:
        path = folder() / "ffmpeg.exe"
    except Exception:
        return None
    return path if path.is_file() else None


def size_mb():
    """The download's size in megabytes, for the question that offers it."""
    return round(SIZE / (1024 * 1024))


def candidates(configured=""):
    """Every place worth looking, in order of preference."""
    found = []
    if configured:
        found.append(Path(configured))
    on_path = shutil.which("ffmpeg")
    if on_path:
        found.append(Path(on_path))
    ours = downloaded()
    if ours is not None:
        found.append(ours)
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
    if configured and not Path(configured).exists():
        return None, _("The configured FFmpeg path does not exist."), set()
    for path in candidates(configured):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        details = describe(path, run)
        if details is not None:
            version, encoders = details
            return path, version, encoders
    return None, _("FFmpeg is not installed."), set()


def _tls():
    """Certificates checked against the bundled authorities.

    A freshly installed Windows does not always have GitHub's issuer in its
    own store, and NVDA's Python reads that store; certifi travels with the
    add-on, so the check holds either way.
    """
    from . import vendors

    try:
        return ssl.create_default_context(cafile=vendors._module("certifi").where())
    except Exception:
        return ssl.create_default_context()


def install(progress=None, cancelled=lambda: False):
    """Download, check and unpack FFmpeg; returns its path.

    ``progress(done, total)`` is called with the bytes downloaded so far, and
    ``cancelled()`` returning True stops the download (:class:`Cancelled`).
    Raises OSError or ValueError on failure.  Everything happens in a scratch
    folder that is removed either way, so a failed or cancelled install never
    leaves a half-written FFmpeg behind for :func:`find` to pick up.
    """
    target = folder()
    work = target.with_name(target.name + ".download")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        archive = work / "ffmpeg.zip"
        digest = hashlib.sha256()
        done = 0
        request = urllib.request.Request(URL, headers={"User-Agent": "Sonos Controller"})
        with (
            urllib.request.urlopen(request, timeout=30, context=_tls()) as response,
            open(archive, "wb") as out,
        ):
            total = int(response.headers.get("Content-Length") or SIZE)
            while True:
                if cancelled():
                    raise Cancelled()
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if digest.hexdigest() != SHA256:
            raise ValueError(_("The download is not the expected FFmpeg."))
        unpacked = work / "unpacked"
        unpacked.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            for name, member in MEMBERS.items():
                with zipped.open(member) as source, open(unpacked / name, "wb") as copy:
                    shutil.copyfileobj(source, copy)
        shutil.rmtree(target, ignore_errors=True)
        unpacked.rename(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target / "ffmpeg.exe"
