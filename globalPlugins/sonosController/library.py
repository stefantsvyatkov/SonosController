"""Local favorites and the history of what the add-on played.

Both lists live in one small JSON file in NVDA's configuration folder, next to
the downloaded FFmpeg, so they follow a portable copy and survive updates of the
add-on.  An entry is a plain dict:

* ``{"kind": "url", "title": ..., "url": ...}`` - an address played with U;
* ``{"kind": "files", "title": ..., "path": ..., "recursive": ...}`` - a file or
  folder played with Control+F;
* ``{"kind": "sonos", "title": ..., "uri": ...}`` - a Sonos favorite, only ever
  in the history: the Sonos favorites themselves belong to the Sonos app.

History entries also keep ``media``, the URI the speaker reported once playback
had started, which is how Shift+F recognises what the add-on is playing.
"""

import json
import os
import threading
from pathlib import Path

from .i18n import _
from .util import valid_url

#: The history keeps this many entries, newest first.
HISTORY_SIZE = 50
#: A sane bound for a hand-kept list; nothing in the layer needs more.
FAVORITES_SIZE = 500
#: The longest name kept, the same as a station's name from its stream.
TITLE_SIZE = 160
FILE_NAME = "sonosController-library.json"
KINDS = ("url", "files", "sonos")


def default_path():
    from NVDAState import WritePaths

    return Path(WritePaths.configDir) / FILE_NAME


def key(entry):
    """What makes two entries the same thing, whatever they are called."""
    if entry["kind"] == "files":
        return ("files", os.path.normcase(os.path.normpath(entry["path"])), bool(entry.get("recursive")))
    return (entry["kind"], entry.get("url") or entry.get("uri"))


def url_entry(url, title=""):
    return {"kind": "url", "title": title or url, "url": url}


def files_entry(path, recursive=False):
    name = os.path.basename(os.path.normpath(path)) or path
    if os.path.isfile(path):
        name = os.path.splitext(name)[0] or name
    return {"kind": "files", "title": name, "path": path, "recursive": bool(recursive)}


def sonos_entry(title, uri):
    return {"kind": "sonos", "title": title, "uri": uri}


def _valid(entry):
    if not isinstance(entry, dict) or entry.get("kind") not in KINDS:
        return False
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        return False
    kind = entry["kind"]
    if kind == "url":
        return valid_url(entry.get("url"))
    if kind == "files":
        return isinstance(entry.get("path"), str) and bool(entry["path"])
    return isinstance(entry.get("uri"), str) and bool(entry["uri"])


class Library:
    def __init__(self, path=None):
        self._path = Path(path) if path else None
        self.lock = threading.Lock()
        self.favorites = []
        self.history = []
        self.loaded = False

    @property
    def path(self):
        if self._path is None:
            self._path = default_path()
        return self._path

    def load(self):
        """Read the file once; a damaged file is set aside rather than overwritten."""
        if self.loaded:
            return
        self.loaded = True
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            try:
                os.replace(self.path, self.path.with_suffix(".json.bad"))
            except OSError:
                pass
            return
        if not isinstance(data, dict):
            return
        self.favorites = [e for e in data.get("favorites", []) if _valid(e)][:FAVORITES_SIZE]
        self.history = [e for e in data.get("history", []) if _valid(e)][:HISTORY_SIZE]

    def save(self):
        data = {"favorites": self.favorites, "history": self.history}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, self.path)

    def _list(self, name):
        return self.favorites if name == "favorites" else self.history

    def find(self, name, wanted):
        return next((entry for entry in self._list(name) if key(entry) == wanted), None)

    def remember(self, entry, media=""):
        """Put what just started at the top of the history; a replay keeps its name."""
        with self.lock:
            self.load()
            wanted = key(entry)
            old = self.find("history", wanted)
            entry = dict(entry, media=media or "")
            if old is not None:
                entry["title"] = old["title"]
                self.history.remove(old)
            self.history.insert(0, entry)
            del self.history[HISTORY_SIZE:]
            self.save()

    def playing(self, media):
        """The newest history entry the speaker is still playing, if any."""
        with self.lock:
            self.load()
            if not media:
                return None
            return next((entry for entry in self.history if entry.get("media") == media), None)

    def add_favorite(self, entry):
        """Add a copy at the end; False when it is already there."""
        with self.lock:
            self.load()
            if self.find("favorites", key(entry)) is not None:
                return False
            if len(self.favorites) >= FAVORITES_SIZE:
                raise RuntimeError(_("Local favorites are full. Remove some first."))
            self.favorites.append({name: value for name, value in entry.items() if name != "media"})
            self.save()
            return True

    def remove(self, name, wanted):
        with self.lock:
            self.load()
            entry = self.find(name, wanted)
            if entry is None:
                return False
            self._list(name).remove(entry)
            self.save()
            return True

    def rename(self, name, wanted, title):
        with self.lock:
            self.load()
            entry = self.find(name, wanted)
            if entry is None:
                return False
            # As long as a station's own name may be, so a paste cannot make
            # every visit to the list read out a page of text.
            entry["title"] = title[:TITLE_SIZE]
            self.save()
            return True
