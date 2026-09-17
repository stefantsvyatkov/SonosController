"""Check keyboard capture and lifecycle using small NVDA test doubles."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class Base:
    def __init__(self):
        self.bindings = {"kb:NVDA+shift+control+s": "toggle"}

    def bindGestures(self, gestures):
        self.bindings.update(gestures)

    def removeGestureBinding(self, key):
        del self.bindings[key]

    def clearGestureBindings(self):
        self.bindings.clear()

    def terminate(self):
        pass


class Configuration(dict):
    spec = {}


class LayerTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        modules = {
            "config": SimpleNamespace(conf=Configuration(sonosController={"selectedRoom": "", "knownAddresses": ""})),
            "globalPluginHandler": SimpleNamespace(GlobalPlugin=Base),
            "globalVars": SimpleNamespace(appArgs=SimpleNamespace(secure=False)),
            "inputCore": SimpleNamespace(
                decide_executeGesture=SimpleNamespace(register=lambda fn: None, unregister=lambda fn: None),
                normalizeGestureIdentifier=lambda key: key.lower()),
            "ui": SimpleNamespace(message=self.messages.append),
            "wx": SimpleNamespace(CallAfter=lambda function, *args: function(*args)),
            "logHandler": SimpleNamespace(log=SimpleNamespace(exception=lambda text: None)),
            "scriptHandler": SimpleNamespace(script=lambda **kwargs: lambda function: function),
        }
        self.patcher = patch.dict(sys.modules, modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        directory = Path(__file__).resolve().parents[1] / "globalPlugins/sonosController"
        spec = importlib.util.spec_from_file_location("sonos_test_plugin", directory / "__init__.py",
                                                     submodule_search_locations=[str(directory)])
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)
        with patch("threading.Thread.start"):
            self.plugin = self.module.GlobalPlugin()

    def test_layer_captures_only_when_active(self):
        self.assertNotIn("kb:upArrow", self.plugin.bindings)
        self.plugin.script_toggle(None)
        self.assertEqual(self.plugin.bindings["kb:control+rightArrow"], "next")
        self.plugin.script_up(None)
        self.assertEqual(self.plugin.tasks.get()[1], "open")
        self.assertEqual(self.plugin.tasks.get()[1], "up")
        self.plugin.script_exit(None)
        self.assertEqual(len(self.plugin.bindings), 1)
        self.assertFalse(self.plugin.active)

    def test_old_results_are_silent_and_queue_is_bounded(self):
        self.plugin.script_toggle(None)
        old = self.plugin.generation
        self.plugin.script_exit(None)
        self.plugin._announce(old, "stale")
        self.assertNotIn("stale", self.messages)
        self.plugin.script_toggle(None)
        for _ in range(20):
            self.plugin.script_up(None)
        self.assertEqual(self.plugin.tasks.qsize(), 8)
        self.assertIn("Sonos is busy", self.messages)
        self.plugin.terminate()
        self.assertTrue(self.plugin.stop.is_set())
        self.assertFalse(self.plugin.bindings)

    def test_secure_mode_has_no_bindings(self):
        self.module.globalVars.appArgs.secure = True
        with patch("threading.Thread.start") as start:
            secure = self.module.GlobalPlugin()
        start.assert_not_called()
        self.assertFalse(secure.bindings)
        secure.terminate()
        self.assertTrue(secure.closed)

    def test_terminate_waits_for_worker_and_closes_local_server(self):
        calls = []

        class Worker:
            alive = True

            def is_alive(self):
                return self.alive

            def join(self, timeout):
                calls.append(("join", timeout))
                self.alive = False

        self.plugin.worker = Worker()
        self.plugin.localFiles = SimpleNamespace(close=lambda: calls.append(("close", None)))
        self.plugin.terminate()
        # A daemon worker that is mid-request must not hold NVDA up on unload.
        self.assertIn(("join", 1.0), calls)
        self.assertIn(("close", None), calls)
        self.assertFalse(self.plugin._inputHookRegistered)

    def test_unknown_key_exits_but_is_allowed_through(self):
        self.plugin.script_toggle(None)
        self.assertTrue(self.plugin._onGesture(SimpleNamespace(isModifier=True)))
        self.assertTrue(self.plugin.active)
        self.assertTrue(self.plugin._onGesture(SimpleNamespace(isModifier=False, normalizedIdentifiers=["kb:b"])))
        self.assertTrue(self.plugin.active)
        self.assertTrue(self.plugin._onGesture(SimpleNamespace(isModifier=False, normalizedIdentifiers=["kb:x"])))
        self.assertFalse(self.plugin.active)
        self.assertEqual(self.plugin.tasks.qsize(), 0)

    def test_worker_recovers_after_rejected_command(self):
        class Rejected(Exception):
            error_code = "402"

        controller = SimpleNamespace(rooms=["preserved"])
        calls = []

        def execute(action):
            calls.append(action)
            if action == "playPause":
                raise Rejected()
            return "Battery 100 percent"

        controller.execute = execute
        self.plugin.active = True
        self.plugin.tasks.put((0, "playPause"))
        self.plugin.tasks.put((0, "battery"))
        self.plugin.tasks.put(None)
        with patch.dict(sys.modules, {"soco": SimpleNamespace(config=SimpleNamespace()), "ifaddr": SimpleNamespace()}):
            with patch.object(self.module, "Controller", return_value=controller):
                self.plugin._work()
        self.assertEqual(calls, ["playPause", "battery"])
        self.assertEqual(controller.rooms, ["preserved"])
        self.assertIn("Battery 100 percent", self.messages)

    def test_transfer_keys_and_immediate_cancellation(self):
        self.plugin.script_toggle(None)
        self.plugin.tasks.get_nowait()
        self.plugin.script_transfer(None)
        self.plugin.script_right(None)
        self.plugin.script_playPause(None)
        self.assertEqual([self.plugin.tasks.get_nowait()[1] for _ in range(3)],
                         ["transferStart", "transferRight", "transferCommit"])
        token = self.plugin.transferCancel
        self.assertTrue(self.plugin._onGesture(SimpleNamespace(isModifier=False, normalizedIdentifiers=["kb:p"])))
        self.assertTrue(token.is_set())
        self.assertTrue(self.plugin.active)
        self.assertFalse(self.plugin.transferMode)

    def test_enter_commits_transfer(self):
        self.plugin.script_toggle(None)
        self.plugin.tasks.get_nowait()
        self.plugin.script_transfer(None)
        self.plugin.script_applyGroup(None)
        self.assertEqual([self.plugin.tasks.get_nowait()[1] for _ in range(2)],
                         ["transferStart", "transferCommit"])
        self.assertTrue(self.plugin._onGesture(
            SimpleNamespace(isModifier=False, normalizedIdentifiers=["kb:enter"])))

    def test_group_keys_space_selects_enter_commits(self):
        self.plugin.script_toggle(None)
        self.plugin.tasks.get_nowait()
        self.plugin.script_group(None)
        self.plugin.script_right(None)
        self.plugin.script_playPause(None)
        self.plugin.script_applyGroup(None)
        self.assertEqual([self.plugin.tasks.get_nowait()[1] for _ in range(4)],
                         ["groupStart", "groupRight", "groupToggle", "groupCommit"])
        self.assertTrue(self.plugin._onGesture(SimpleNamespace(isModifier=False, normalizedIdentifiers=["kb:enter"])))
        self.assertTrue(self.plugin.groupMode)
        self.plugin.script_exit(None)
        self.assertTrue(self.plugin.transferCancel.is_set())
        self.assertFalse(self.plugin.groupMode)

    def test_settings_navigation_and_exit(self):
        self.plugin.script_toggle(None)
        self.plugin.tasks.get_nowait()
        self.plugin.script_settings(None)
        self.plugin.script_down(None)
        self.plugin.script_right(None)
        self.assertEqual([self.plugin.tasks.get_nowait()[1] for _ in range(3)],
                         ["settingsStart", "settingsDown", "settingsRight"])
        self.plugin.script_settings(None)
        self.assertFalse(self.plugin.settingsMode)
        self.assertTrue(self.plugin.active)

    def test_favorites_timer_and_alarm_routing(self):
        for starter, expected, actions in (
            ("favorites", "menuFavoritesStart", [("down", "menuDown"), ("applyGroup", "menuEnter")]),
            ("sleepTimer", "menuTimerStart", [("left", "menuLeft"), ("applyGroup", "menuEnter")]),
            ("alarms", "menuAlarmsStart", [("newAlarm", "menuNew"), ("deleteAlarm", "menuDelete")]),
        ):
            self.plugin.script_toggle(None)
            self.plugin.tasks.get_nowait()
            getattr(self.plugin, "script_" + starter)(None)
            self.assertEqual(self.plugin.tasks.get_nowait()[1], expected)
            for name, action in actions:
                getattr(self.plugin, "script_" + name)(None)
                self.assertEqual(self.plugin.tasks.get_nowait()[1], action)
            self.plugin.script_exit(None)

    def test_double_p_and_stale_clipboard_result(self):
        self.plugin.script_toggle(None)
        self.plugin.tasks.get_nowait()
        self.plugin.script_playing(None)
        with patch.object(self.module.scriptHandler, "getLastScriptRepeatCount", return_value=1, create=True):
            self.plugin.script_playing(None)
        self.assertEqual([self.plugin.tasks.get_nowait()[1] for _ in range(2)], ["playing", "copyTrack"])
        copied = []
        generation = self.plugin.generation
        with patch.dict(sys.modules, {"api": SimpleNamespace(copyToClip=copied.append)}):
            self.plugin._copyTrack(generation, "Song")
            self.plugin.script_exit(None)
            self.plugin._copyTrack(generation, "Stale")
        self.assertEqual(copied, ["Song"])

    def test_dialog_releases_and_restores_layer_bindings(self):
        self.plugin.script_toggle(None)
        events = []
        frame = SimpleNamespace(prePopup=lambda: events.append("open"), postPopup=lambda: events.append("close"))
        def callback(parent):
            self.assertIs(parent, frame)
            self.assertNotIn("kb:space", self.plugin.bindings)
            self.assertTrue(self.plugin.dialogOpen)
            self.plugin._onGesture(SimpleNamespace(isModifier=False, normalizedIdentifiers=["kb:x"]))
            self.assertTrue(self.plugin.active)
        with patch.dict(sys.modules, {"gui": SimpleNamespace(mainFrame=frame)}):
            self.plugin._modal(self.plugin.generation, callback)
        self.assertEqual(events, ["open", "close"])
        self.assertIn("kb:space", self.plugin.bindings)
        self.assertFalse(self.plugin.dialogOpen)

    def test_local_files_and_favorites_have_separate_keys(self):
        self.plugin.script_toggle(None)
        self.assertEqual(self.plugin.bindings["kb:control+f"], "localFiles")
        self.assertEqual(self.plugin.bindings["kb:f"], "favorites")
        self.assertEqual(self.plugin.bindings["kb:control+p"], "playbackSettings")
        self.plugin.tasks.get_nowait()
        self.plugin.script_localFiles(None)
        self.assertEqual(self.plugin.tasks.get_nowait()[1], "localStart")
        self.plugin.script_playbackSettings(None)
        self.plugin.script_right(None)
        self.assertEqual([self.plugin.tasks.get_nowait()[1] for _ in range(2)], ["playbackSettingsStart", "settingsRight"])

    def test_full_queue_does_not_leave_unstarted_mode(self):
        self.plugin.script_toggle(None)
        for _ in range(8):
            self.plugin.script_up(None)
        self.plugin.script_alarms(None)
        self.assertIsNone(self.plugin.menuMode)
        self.plugin.script_settings(None)
        self.assertFalse(self.plugin.settingsMode)

    def test_nested_modal_is_ignored(self):
        self.plugin.script_toggle(None)
        self.plugin.dialogOpen = True
        calls = []
        self.plugin._modal(self.plugin.generation, calls.append)
        self.assertEqual(calls, [])

    def test_escape_returns_to_main_then_exits(self):
        self.plugin.script_toggle(None)
        self.plugin.script_favorites(None)
        old = self.plugin.generation
        self.plugin.script_back(None)
        self.assertTrue(self.plugin.active)
        self.assertIsNone(self.plugin.menuMode)
        self.assertGreater(self.plugin.generation, old)
        self.assertEqual(self.plugin.tasks.qsize(), 0)
        self.plugin.script_up(None)
        self.assertEqual(self.plugin.tasks.get_nowait()[1], "up")
        self.plugin.script_back(None)
        self.assertFalse(self.plugin.active)

    def test_favorite_enter_is_captured_and_main_command_leaves_submode(self):
        self.plugin.script_toggle(None)
        self.plugin.script_favorites(None)
        self.plugin._onGesture(SimpleNamespace(isModifier=False, normalizedIdentifiers=["kb:enter"]))
        self.assertEqual(self.plugin.menuMode, "favorites")
        self.plugin._onGesture(SimpleNamespace(isModifier=False, normalizedIdentifiers=["kb:b"]))
        self.assertIsNone(self.plugin.menuMode)
        self.assertTrue(self.plugin.active)
        self.plugin.script_battery(None)
        self.assertEqual(self.plugin.tasks.get_nowait()[1], "battery")





    def test_connecting_is_announced_on_every_entry(self):
        self.plugin.script_toggle(None)
        self.plugin.script_exit(None)
        self.plugin.script_toggle(None)
        self.assertEqual(self.messages.count('Connecting.'), 2)

    def test_old_configuration_without_address_cache_starts(self):
        # Existing users upgrade without a knownAddresses key.
        self.plugin.knownAddresses = []
        self.assertEqual(self.plugin.selected, "")

