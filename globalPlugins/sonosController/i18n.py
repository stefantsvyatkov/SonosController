"""Translation setup, kept in one place.

Every module imports ``_`` from here, so NVDA's gettext catalogue is installed
exactly once and the add-on still imports outside NVDA (tests, tooling).  The
shipped interface is English; wrapping the strings only makes a translation
possible later without touching the code again.
"""

import gettext

try:
    import addonHandler
except ImportError:  # running outside NVDA
    addonHandler = None

if addonHandler is not None:
    # Installs ``_`` into this module's namespace for the add-on's catalogue.
    addonHandler.initTranslation()

if "_" not in globals():
    _ = gettext.NullTranslations().gettext

__all__ = ["_"]
