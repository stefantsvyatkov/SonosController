"""Headless NVDA + Sonos emulator for testing the add-on without hardware.

The emulator loads the real, unmodified ``globalPlugins.sonosController``
package against fake NVDA modules and a simulated Sonos household, so a test
can press keys and read back exactly what NVDA would speak.
"""

from .session import Session, Speech
from .sonos import Household, SimPlayer

__all__ = ["Session", "Speech", "Household", "SimPlayer"]
