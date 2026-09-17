"""Every module that speaks to the user must be able to translate its strings.

Linters cannot help here: ruff treats a bare ``_`` as a throwaway name and does
not count its uses, so it will happily delete a real import.  Checking the
structure directly is both cheaper and more reliable, and it catches the case
that actually hurts - a NameError raised only on an error path nobody tests.
"""

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import load

ADDON = Path(__file__).resolve().parents[1] / "globalPlugins" / "sonosController"


def addon_modules():
    return sorted(p for p in ADDON.glob("*.py"))


class TranslationImports(unittest.TestCase):
    def test_modules_that_call_underscore_import_it(self):
        missing = []
        for path in addon_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls = any(isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "_"
                        for node in ast.walk(tree))
            if not calls:
                continue
            imported = any(
                isinstance(node, ast.ImportFrom)
                and node.module in ("i18n", "sonosController.i18n")
                and any(alias.name == "_" for alias in node.names)
                for node in ast.walk(tree))
            defines = path.name == "i18n.py"
            if not (imported or defines):
                missing.append(path.name)
        self.assertEqual(missing, [], "these call _() without importing it")

    def test_no_function_shadows_the_translation_name(self):
        """A local ``_`` silently turns every _() call in that function into a bug.

        This happened for real: a ``ready, _, _ = select.select(...)`` unpack
        made the module's gettext lookup a local list, so the error message it
        guarded would have raised TypeError instead.
        """
        offenders = []
        for path in addon_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = any(
                isinstance(node, ast.ImportFrom)
                and node.module in ("i18n", "sonosController.i18n")
                and any(alias.name == "_" for alias in node.names)
                for node in ast.walk(tree))
            if not imports:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Name) and inner.id == "_"
                            and isinstance(inner.ctx, ast.Store)):
                        offenders.append(f"{path.name}:{inner.lineno} in {node.name}()")
        self.assertEqual(offenders, [], "these rebind _ and break translation there")

    def test_every_module_imports_cleanly(self):
        for path in addon_modules():
            if path.name in ("__init__.py", "dialogs.py", "controls.py", "relayhooks.py"):
                continue  # need NVDA or wx
            with self.subTest(module=path.stem):
                load(path.stem)

    def test_i18n_provides_a_usable_underscore(self):
        self.assertEqual(load("i18n")._("Sonos"), "Sonos")


if __name__ == "__main__":
    unittest.main()
