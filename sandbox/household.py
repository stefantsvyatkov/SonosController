"""Puts the simulated household behind the real add-on inside Windows Sandbox.

The sandbox has no Sonos speakers, and it cannot reach the ones on the house
network either: its network is behind its own virtual switch, so SSDP never
leaves it.  What the sandbox is for is the half the host tests cannot reach -
real NVDA, real wxPython, real speech - so the speakers are the part that gets
simulated, not the screen reader.

The agent stages this module and the emulator's simulator into the add-on's
own folder as ``_sandbox_household.py`` and ``_sandbox_sonos.py``, then appends
four lines to the staged ``vendors.py`` and ``discovery.py`` that call in here
when ``SONOS_SIM`` is set.  Only the staged copy is touched; the add-on in the
repository is never modified, and without the variable these files do nothing.

``SONOS_SIM`` names the household: ``default`` is the one the host tests use,
``empty`` has no speakers at all, for the "No Sonos rooms found" path.
"""

import os
import types

from . import _sandbox_sonos as simulator


_household = None
_alarms = None


def household():
    """The one household, built on first use."""
    global _household
    if _household is None:
        if os.environ.get("SONOS_SIM") == "empty":
            _household = simulator.Household()
        else:
            _household = simulator.default_household()
    return _household


def addresses():
    """The speakers' addresses, for the add-on's saved-address reconnect."""
    return [player.ip_address for player in household().players]


def patch_vendors(namespace):
    """Replace the private SoCo and ifaddr with the simulator's."""
    global _alarms
    house = household()
    soco = simulator.build_soco_module(house)
    _alarms = simulator.build_alarms_module(
        house, lambda uid: next((p for p in house.players if p.uid == uid), None))
    _alarms.reset()
    ifaddr = types.ModuleType("ifaddr")
    ifaddr.get_adapters = lambda: []

    namespace["load"] = lambda: (soco, ifaddr)
    namespace["soco"] = lambda: soco
    namespace["ifaddr"] = lambda: ifaddr
    namespace["soco_submodule"] = lambda name: (
        _alarms if name == "alarms" else getattr(soco, name))


def patch_discovery(namespace):
    """Answer discovery from the simulator instead of the network.

    SSDP itself is the one thing the sandbox cannot exercise, so it is stubbed
    here exactly as the host emulator stubs it; the saved-address reconnect
    path still runs the add-on's own code.
    """
    house = household()
    known = {player.ip_address for player in house.players}
    namespace["reachable"] = lambda ip: ip in known
    namespace["discover_rooms"] = lambda soco, timeout=4: [
        player for player in house.players if player.is_visible]
