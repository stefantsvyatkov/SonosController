"""Verify that bundled Sonos dependencies stay in the add-on namespace."""

from pathlib import Path
import hashlib
import subprocess
import sys
import textwrap
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "globalPlugins" / "sonosController"


def run_isolated(code):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=PLUGIN,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise AssertionError(
            f"Isolated Python process failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


class VendorIsolationTests(unittest.TestCase):
    def test_preloaded_foreign_dependencies_are_untouched(self):
        output = run_isolated(
            r'''
            import sys
            from pathlib import Path
            from types import ModuleType

            sys.path.insert(0, str(Path.cwd()))
            roots = ("soco", "requests", "urllib3", "ifaddr", "lxml",
                     "xmltodict", "appdirs", "certifi", "charset_normalizer", "idna")
            optional = ("brotli", "brotlicffi", "socks", "cryptography", "OpenSSL",
                        "backports", "win32api", "win32com", "h2", "aiohttp", "twisted")
            foreign = {}
            for name in roots + optional:
                module = ModuleType(name)
                module.marker = object()
                module.__path__ = []
                foreign[name] = module
                sys.modules[name] = module

            import vendors
            private_soco, private_ifaddr = vendors.load()
            private_requests = vendors.requests()
            vendors._module("appdirs")

            assert private_soco.__name__ == "_vendor.soco"
            assert private_ifaddr.__name__ == "_vendor.ifaddr"
            assert private_requests.__name__ == "_vendor.requests"
            for name, module in foreign.items():
                assert sys.modules[name] is module, name
            assert private_soco is not foreign["soco"]
            assert private_requests is not foreign["requests"]
            assert private_ifaddr is not foreign["ifaddr"]
            for module_name, module in sys.modules.items():
                if module_name.startswith("_vendor."):
                    assert not any(
                        value is foreign_module
                        for value in vars(module).values()
                        for foreign_module in foreign.values()
                    ), module_name
            print("foreign-preserved")
            '''
        )
        self.assertIn("foreign-preserved", output)

    def test_loading_addon_first_does_not_claim_top_level_names(self):
        output = run_isolated(
            r'''
            import sys
            from pathlib import Path
            from types import ModuleType

            sys.path.insert(0, str(Path.cwd()))
            roots = ("soco", "requests", "urllib3", "ifaddr", "lxml",
                     "xmltodict", "appdirs", "certifi", "charset_normalizer", "idna")
            import vendors
            private_soco, private_ifaddr = vendors.load()
            private_requests = vendors.requests()
            assert not any(name.split('.', 1)[0] in roots for name in sys.modules)

            foreign = {}
            for name in roots:
                module = ModuleType(name)
                module.marker = object()
                foreign[name] = module
                sys.modules[name] = module
            for name, module in foreign.items():
                assert sys.modules[name] is module, name
            assert vendors.load() == (private_soco, private_ifaddr)
            assert vendors.requests() is private_requests
            print("addon-first-isolated")
            '''
        )
        self.assertIn("addon-first-isolated", output)

    def test_reload_and_concurrent_imports_are_stable(self):
        output = run_isolated(
            r'''
            import importlib
            import sys
            from concurrent.futures import ThreadPoolExecutor
            from pathlib import Path

            sys.path.insert(0, str(Path.cwd()))
            roots = ("soco", "requests", "urllib3", "ifaddr", "lxml",
                     "xmltodict", "appdirs", "certifi", "charset_normalizer", "idna")
            import vendors

            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(lambda _: vendors.load(), range(64)))
            first = results[0]
            assert all(result[0] is first[0] and result[1] is first[1] for result in results)
            assert all(name not in sys.modules for name in roots)

            importlib.reload(vendors)
            second = vendors.load()
            assert second[0] is first[0] and second[1] is first[1]
            assert not any(name.split('.', 1)[0] in roots for name in sys.modules)
            print("reload-concurrency-stable")
            '''
        )
        self.assertIn("reload-concurrency-stable", output)

    def test_private_package_can_be_unloaded_and_imported_again(self):
        output = run_isolated(
            r'''
            import gc
            import importlib
            import sys
            from pathlib import Path
            from types import ModuleType

            roots = ("soco", "requests", "urllib3", "ifaddr", "lxml",
                     "xmltodict", "appdirs", "certifi", "charset_normalizer", "idna")
            foreign = {}
            for name in roots:
                module = ModuleType(name)
                module.marker = object()
                foreign[name] = module
                sys.modules[name] = module

            def package():
                module = ModuleType("isolated_addon")
                module.__path__ = [str(Path.cwd())]
                module.__package__ = "isolated_addon"
                sys.modules[module.__name__] = module

            package()
            first = importlib.import_module("isolated_addon.vendors").load()
            first_names = {name for name in sys.modules if name.startswith("isolated_addon.")}
            assert "isolated_addon._vendor.soco" in first_names
            for name in list(first_names):
                del sys.modules[name]
            del sys.modules["isolated_addon"]
            del first
            gc.collect()

            package()
            second = importlib.import_module("isolated_addon.vendors").load()
            assert second[0].__name__ == "isolated_addon._vendor.soco"
            for name, module in foreign.items():
                assert sys.modules[name] is module, name
            print("unload-reload-isolated")
            '''
        )
        self.assertIn("unload-reload-isolated", output)

    def test_stdlib_xml_replacements_cover_runtime_paths(self):
        output = run_isolated(
            r'''
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path.cwd()))
            from _vendor.soco.data_structures_entry import from_didl_string
            from _vendor.soco.zonegroupstate import normalize_zgs_xml

            didl = (
                '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" '
                'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                '<item id="1" parentID="0" restricted="true">'
                '<dc:title>A & B&#1;</dc:title>'
                '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
                '</item></DIDL-Lite>'
            )
            items = from_didl_string(didl)
            assert len(items) == 1 and items[0].title == "A & B"

            first = normalize_zgs_xml(
                '<ZoneGroupState> text <ZoneGroups>'
                '<ZoneGroup Coordinator="B"><ZoneGroupMember UUID="D"/>'
                '<ZoneGroupMember UUID="C"/></ZoneGroup>'
                '<ZoneGroup Coordinator="A"><ZoneGroupMember UUID="B"/>'
                '<ZoneGroupMember UUID="A"/></ZoneGroup>'
                '</ZoneGroups></ZoneGroupState>'
            )
            second = normalize_zgs_xml(
                '<ZoneGroupState><ZoneGroups>'
                '<ZoneGroup Coordinator="A"><ZoneGroupMember UUID="A"/>'
                '<ZoneGroupMember UUID="B"/></ZoneGroup>'
                '<ZoneGroup Coordinator="B"><ZoneGroupMember UUID="C"/>'
                '<ZoneGroupMember UUID="D"/></ZoneGroup>'
                '</ZoneGroups></ZoneGroupState>'
            )
            from xml.etree import ElementTree as ET
            assert ET.tostring(first) == ET.tostring(second)
            assert "lxml" not in sys.modules
            print("stdlib-xml-ok")
            '''
        )
        self.assertIn("stdlib-xml-ok", output)

    def test_generated_tree_is_current_and_contains_no_native_extensions(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "vendorize.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        vendor = PLUGIN / "_vendor"
        self.assertFalse((vendor / "lxml").exists())
        binaries = [
            path for path in vendor.rglob("*")
            if path.suffix.lower() in {".pyd", ".dll", ".exe"}
        ]
        self.assertEqual(binaries, [])

    def test_build_is_reproducible_and_excludes_source_vendor_tree(self):
        """Build twice and compare, using the path the build itself reports.

        This used to hash a hard-coded name in the project root.  The build has
        written into build/ for a while now, so the test was hashing a package
        left over from an earlier version: the two digests always matched
        because nothing had rebuilt that file, and the content checks below
        were inspecting a stale archive.  Taking the path from the build's own
        output means the two cannot drift apart again.
        """
        command = [sys.executable, str(ROOT / "tools" / "build.py")]
        digests = []
        packages = []
        for _ in range(2):
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            package = Path(result.stdout.strip().splitlines()[-1])
            self.assertTrue(package.is_file(), f"the build reported {package}, which is not there")
            packages.append(package)
            digests.append(hashlib.sha256(package.read_bytes()).hexdigest())
        self.assertEqual(packages[0], packages[1], "the build moved its output between runs")
        self.assertEqual(digests[0], digests[1], "two identical builds differ")

        with zipfile.ZipFile(packages[0]) as archive:
            names = archive.namelist()
        self.assertIn("globalPlugins/sonosController/_vendor/vendor-manifest.json", names)
        self.assertFalse(any(name.startswith("globalPlugins/sonosController/lib/") for name in names))
        native = {
            name for name in names
            if Path(name).suffix.lower() in {".pyd", ".dll", ".exe"}
        }
        self.assertEqual(native, set(), "the public package must carry no executables")
        # relay.py and relay_urls.py are part of the public package: pure Python
        # that does nothing until the machine has an FFmpeg of its own.  The
        # guard that matters is the one above, that nothing executable ships.
        self.assertIn("globalPlugins/sonosController/relay.py", names)
        self.assertIn("globalPlugins/sonosController/relay_urls.py", names)
        # What must never ship is the executable itself, nor its licence folder,
        # which belong only to the personal build.
        self.assertFalse(any(name.startswith("globalPlugins/sonosController/bin/") for name in names))
        self.assertFalse(any(name.startswith("thirdParty/ffmpeg/") for name in names))
        # The two packages used to be told apart by scanning the text for
        # "ffmpeg" and "relaymanager".  Both words belong in the public package
        # now: it looks for an FFmpeg the machine already has, and says so in
        # its own documentation.  What separates the personal build is the
        # executable it adds, so check for that by name as well as by folder,
        # in case a future build puts it somewhere else.
        forbidden = {"ffmpeg.exe", "ffmpeg.sha256", "ffmpeg-components.txt",
                     "ffmpeg-imports.txt"}
        shipped = {Path(name).name.lower() for name in names}
        self.assertEqual(shipped & forbidden, set(),
                         "the public package must not carry a bundled FFmpeg")


if __name__ == "__main__":
    unittest.main()
