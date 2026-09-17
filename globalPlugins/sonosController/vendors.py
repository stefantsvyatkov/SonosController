"""Load Sonos Controller's private dependency namespace."""

import importlib
import threading


_lock = threading.RLock()
_loaded = None
_package = (__package__ + ".") if __package__ else ""
_vendor = _package + "_vendor"


def _module(name):
    return importlib.import_module(f"{_vendor}.{name}")


def load():
    """Return the private SoCo and ifaddr modules."""
    global _loaded
    with _lock:
        if _loaded is None:
            soco_module = _module("soco")
            ifaddr_module = _module("ifaddr")
            soco_module.config.REQUEST_TIMEOUT = 3
            _loaded = (soco_module, ifaddr_module)
        return _loaded


def soco():
    return load()[0]


def ifaddr():
    return load()[1]


def requests():
    return _module("requests")


def soco_submodule(name):
    if not name or "." in name:
        raise ValueError("Expected one SoCo submodule name")
    load()
    return _module(f"soco.{name}")
