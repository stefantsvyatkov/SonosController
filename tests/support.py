"""Import add-on modules in tests without NVDA installed.

The add-on is a package that uses relative imports, so a test cannot simply
load a single file.  This mounts the add-on directory under a private package
name; from there ``from . import vendors`` and friends resolve normally.

``i18n`` already falls back to a pass-through ``_`` when ``addonHandler`` is
missing, so nothing else has to be stubbed for the non-GUI modules.
"""

import importlib
import sys
import types
from pathlib import Path

ADDON = Path(__file__).resolve().parents[1] / "globalPlugins" / "sonosController"
PACKAGE = "sonosAddon"


def _mount():
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(ADDON)]
        package.__doc__ = "Add-on modules mounted for testing."
        sys.modules[PACKAGE] = package
    return sys.modules[PACKAGE]


def load(name):
    """Import one add-on module, for example ``load("backend")``."""
    _mount()
    return importlib.import_module(f"{PACKAGE}.{name}")
