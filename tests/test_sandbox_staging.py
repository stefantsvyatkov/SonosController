"""The sandbox's simulated household, checked without starting a sandbox.

``sandbox/SandboxAgent.ps1`` puts the simulator behind the add-on by copying two
modules into the staged package and appending four lines to ``vendors.py`` and
``discovery.py``.  That hook is easy to break from either side - a rename in
the add-on, an edit to the agent - and a break would only show up as a sandbox
run that quietly talked to nothing.  These tests do the same staging on the
host and import the result, so the hook is checked by the ordinary suite.

The appended lines are kept here in one place and read by the test, so this
file and the agent cannot drift apart silently: if they do, the test fails.
"""

import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "globalPlugins" / "sonosController"
AGENT = ROOT / "sandbox" / "SandboxAgent.ps1"

#: The hook the agent appends, as it appends it.
HOOK = [
    "",
    "import os as _os",
    "if _os.environ.get('SONOS_SIM'):",
    "    from . import _sandbox_household as _sandbox",
    "    _sandbox.patch_vendors(globals())",
]


class Staging(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.package = Path(self.temporary.name) / "stagedAddon"
        shutil.copytree(SOURCE, self.package,
                        ignore=shutil.ignore_patterns("__pycache__", "_vendor"))
        shutil.copy(ROOT / "sandbox" / "household.py", self.package / "_sandbox_household.py")
        shutil.copy(ROOT / "tools" / "harness" / "sonos.py", self.package / "_sandbox_sonos.py")
        self.append(self.package / "vendors.py", HOOK)
        self.append(self.package / "discovery.py",
                    HOOK[:-1] + ["    _sandbox.patch_discovery(globals())"])

        self.name = "stagedAddon"
        mount = types.ModuleType(self.name)
        mount.__path__ = [str(self.package)]
        sys.modules[self.name] = mount
        self.addCleanup(self.unmount)

    def append(self, path, lines):
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def unmount(self):
        for name in [n for n in sys.modules if n == self.name or n.startswith(self.name + ".")]:
            del sys.modules[name]

    def module(self, name, simulated=True):
        """Import one staged module with or without the sandbox variable set."""
        previous = os.environ.get("SONOS_SIM")
        if simulated:
            os.environ["SONOS_SIM"] = "default"
        else:
            os.environ.pop("SONOS_SIM", None)
        try:
            return importlib.import_module(f"{self.name}.{name}")
        finally:
            if previous is None:
                os.environ.pop("SONOS_SIM", None)
            else:
                os.environ["SONOS_SIM"] = previous

    def test_the_hook_matches_the_one_the_agent_appends(self):
        agent = AGENT.read_text(encoding="utf-8")
        for line in HOOK[1:] + ["    _sandbox.patch_discovery(globals())"]:
            self.assertIn(f'"{line}"', agent,
                          "SandboxAgent.ps1 no longer appends this line")

    def test_vendors_answers_from_the_simulator(self):
        vendors = self.module("vendors")
        soco, ifaddr = vendors.load()
        # The simulator's module, not the private SoCo: its SoCo() answers
        # only for the simulated addresses, and discover() returns the
        # household rather than putting SSDP on a network with no speakers.
        self.assertEqual([player.player_name for player in soco.discover()][:1],
                         ["Living Room"])
        with self.assertRaises(OSError):
            soco.SoCo("10.0.0.1")
        self.assertEqual(ifaddr.get_adapters(), [])
        self.assertIs(vendors.soco(), soco)
        alarms = vendors.soco_submodule("alarms")
        self.assertTrue(hasattr(alarms, "Alarm") and hasattr(alarms, "Alarms"), dir(alarms))

    def test_discovery_answers_with_the_simulated_rooms(self):
        vendors = self.module("vendors")
        discovery = self.module("discovery")
        rooms = discovery.discover_rooms(vendors.soco())
        self.assertEqual(sorted(room.player_name for room in rooms),
                         ["Kitchen", "Living Room", "Office", "TV Room"])
        self.assertTrue(discovery.reachable(rooms[0].ip_address))
        self.assertFalse(discovery.reachable("10.0.0.1"))

    def test_an_empty_household_has_no_rooms(self):
        os.environ["SONOS_SIM"] = "empty"
        self.addCleanup(os.environ.pop, "SONOS_SIM", None)
        vendors = importlib.import_module(f"{self.name}.vendors")
        discovery = importlib.import_module(f"{self.name}.discovery")
        self.assertEqual(discovery.discover_rooms(vendors.soco()), [])

    def test_without_the_variable_the_staged_add_on_is_untouched(self):
        """The hook is inert, so the staged package is the published one."""
        vendors = self.module("vendors", simulated=False)
        # Nothing was replaced, so loading would reach for the real _vendor
        # tree, which this staged copy deliberately does not have.
        with self.assertRaises(ImportError):
            vendors.load()


if __name__ == "__main__":
    unittest.main()
