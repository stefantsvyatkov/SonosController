"""End-to-end checks driven through the NVDA emulator.

These run the real add-on the way a user does: a key press goes through
``decide_executeGesture``, then through the gesture bindings, into the worker
thread, and back out as speech.

``FixedDefects`` covers the problems the code review found, asserted the way
they are meant to behave, and ``Cost`` puts a ceiling on how many speaker
requests a key press may make.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from harness import Session
from harness.session import default_household


class EmulatorTest(unittest.TestCase):
    def session(self, **kwargs):
        session = Session(**kwargs)
        self.addCleanup(session.close)
        return session

    def layer(self, **kwargs):
        session = self.session(**kwargs)
        session.press("kb:NVDA+shift+control+s")
        return session


class LayerBasics(EmulatorTest):
    def test_toggle_announces_the_selected_room(self):
        session = self.layer()
        self.assertEqual(session.speech[0], "Connecting.")
        self.assertIn("Sonos,", session.speech[1])
        self.assertEqual(session.mode, "layer")

    def test_volume_up_and_down(self):
        session = self.layer()
        up = session.press("kb:upArrow")[-1]
        down = session.press("kb:downArrow")[-1]
        self.assertEqual(up, "26 percent")
        self.assertEqual(down, "25 percent")

    def test_left_and_right_move_between_groups(self):
        session = self.layer()
        first = session.press("kb:rightArrow")[-1]
        back = session.press("kb:leftArrow")[-1]
        self.assertNotEqual(first, back)

    def test_double_p_copies_the_track(self):
        session = self.layer()
        session.select_room("Living Room")
        session.press("kb:p")
        session.press("kb:p")
        self.assertEqual(session.clipboard.get("value"), "Blue Monday — New Order")
        self.assertIn("Copied.", session.speech)

    def test_unrelated_key_leaves_the_layer_and_reaches_windows(self):
        session = self.layer()
        heard = session.press("kb:x")
        self.assertEqual(session.mode, "off")
        self.assertIn("<passed to Windows: kb:x>", heard)

    def test_escape_in_a_submode_returns_to_the_layer(self):
        session = self.layer()
        session.press("kb:f")
        self.assertEqual(session.mode, "menu:favorites")
        session.press("kb:escape")
        self.assertEqual(session.mode, "layer")

    def test_secure_mode_binds_nothing(self):
        session = self.session(secure=True)
        self.assertEqual(session.plugin.worker, None)
        self.assertEqual(session.bound_keys, [])


class Discovery(EmulatorTest):
    def test_saved_addresses_skip_the_broadcast_scan(self):
        house = default_household()
        session = self.session(household=house,
                               addresses=[p.ip_address for p in house.players])
        session.press("kb:NVDA+shift+control+s")
        self.assertIn("Sonos,", session.speech[1])
        # The fast path polls topology instead of running SSDP.
        self.assertIn("all_zones", house.calls)

    def test_addresses_are_remembered_after_discovery(self):
        session = self.layer()
        saved = session.conf["sonosController"]["knownAddresses"]
        self.assertEqual(sorted(saved.split(",")),
                         sorted(p.ip_address for p in session.household.players))


class Menus(EmulatorTest):
    def test_favorites_play_and_return(self):
        session = self.layer()
        session.press("kb:f")
        heard = session.press("kb:enter")[-1]
        self.assertTrue(heard.startswith(("Playing", "Starting")), heard)
        self.assertEqual(session.mode, "layer")

    def test_alarm_list_reads_the_existing_alarm(self):
        session = self.layer()
        heard = session.press("kb:a")[-1]
        self.assertIn("07:30", heard)
        self.assertIn("Living Room", heard)
        self.assertIn("WEEKDAYS", heard)

    def test_space_toggles_an_alarm(self):
        session = self.layer()
        session.press("kb:a")
        heard = session.press("kb:space")[-1]
        self.assertIn("off", heard)

    def test_sleep_timer_presets_move_with_left_and_right(self):
        session = self.layer()
        session.press("kb:s")
        self.assertEqual(session.press("kb:rightArrow")[-1], "5 minutes")
        self.assertEqual(session.press("kb:leftArrow")[-1], "Timer off")


class Grouping(EmulatorTest):
    def test_group_selection_applies(self):
        house = default_household()
        session = self.layer(household=house)
        session.select_room("Living Room")
        session.press("kb:g")
        session.press("kb:rightArrow", "kb:space")
        heard = session.press("kb:enter")[-1]
        self.assertTrue(heard.startswith("Playing rooms:"), heard)
        self.assertEqual(session.mode, "layer")

    def test_transfer_moves_playback(self):
        house = default_household()
        session = self.layer(household=house)
        session.select_room("Living Room")
        session.press("kb:t")
        heard = session.press("kb:space")[-1]
        self.assertIn("transferred", heard)
        self.assertEqual(house.find("Kitchen").playback.state, "PLAYING")


class Errors(EmulatorTest):
    def test_upnp_error_code_is_reported_plainly(self):
        from harness.sonos import SoCoUPnPException
        house = default_household()
        session = self.layer(household=house)
        session.select_room("Living Room")
        house.fail("Pause", SoCoUPnPException("no", error_code="701"))
        self.assertEqual(session.press("kb:space")[-1],
                         "Command unavailable. Sonos error 701.")

    def test_network_failure_does_not_leak_a_traceback(self):
        house = default_household()
        session = self.layer(household=house)
        house.fail("GetPositionInfo", OSError("unreachable"))
        self.assertEqual(session.press("kb:p")[-1], "Command failed. Try again.")

    def test_no_rooms_is_explained(self):
        house = default_household()
        house.players.clear()
        session = self.session(household=house)
        heard = session.press("kb:NVDA+shift+control+s")
        self.assertIn("No Sonos rooms found.", heard)


class FixedDefects(EmulatorTest):
    """Behaviour the review found wrong, now asserted the right way round."""

    def test_up_arrow_in_group_mode_keeps_the_selection(self):
        session = self.layer()
        session.select_room("Living Room")
        session.press("kb:g")
        session.press("kb:rightArrow", "kb:space")
        heard = session.press("kb:upArrow")
        self.assertEqual(session.mode, "group", "grouping must survive a volume key")
        self.assertIn("percent", heard[-1])
        # The selection is still live, so Enter still applies it.
        self.assertTrue(session.press("kb:enter")[-1].startswith("Playing rooms:"))

    def test_up_arrow_in_transfer_mode_keeps_the_selection(self):
        session = self.layer()
        session.select_room("Living Room")
        session.press("kb:t")
        heard = session.press("kb:downArrow")
        self.assertEqual(session.mode, "transfer")
        self.assertIn("percent", heard[-1])
        self.assertIn("transferred", session.press("kb:space")[-1])

    def numeric_field(self, name):
        """Walk one numeric alarm field and report what each key did."""
        session = self.layer()
        session.press("kb:a")
        seen = {}

        def inspect(dialog):
            control = dialog.focus_on(name)
            seen["role"] = control.role
            seen["start"] = self.number(control)
            dialog.press("up")
            seen["up"] = self.number(control)
            dialog.press("down")
            seen["down"] = self.number(control)
            dialog.press("pageup")
            seen["pageup"] = self.number(control)
            dialog.press("pagedown")
            seen["pagedown"] = self.number(control)
            return False

        session.on_dialog(inspect)
        session.press("kb:enter")
        return seen

    @staticmethod
    def number(control):
        value = getattr(control, "GetValue", None)
        return value() if value else int(control.GetStringSelection())

    def test_alarm_volume_slider_follows_the_arrows(self):
        seen = self.numeric_field("Volume (%)")
        self.assertEqual(seen["role"], "slider", "it should still be an ordinary slider")
        self.assertEqual(seen["up"], seen["start"] + 1, "Up must raise the volume")
        self.assertEqual(seen["down"], seen["start"], "Down must lower it again")

    def test_alarm_hour_dropdown_counts_up_on_up(self):
        seen = self.numeric_field("Hour")
        self.assertEqual(seen["role"], "combo box", "it should still be a dropdown")
        self.assertEqual(seen["up"], seen["start"] + 1, "Up must move to a later hour")
        self.assertEqual(seen["down"], seen["start"])

    def test_page_keys_move_five_in_the_arrows_direction(self):
        for name in ("Volume (%)", "Hour", "Minute"):
            with self.subTest(field=name):
                seen = self.numeric_field(name)
                self.assertEqual(seen["pageup"], seen["start"] + 5,
                                 "Page Up must add five, like five presses of Up")
                self.assertEqual(seen["pagedown"], seen["start"],
                                 "Page Down must undo it")

    def test_sleep_timer_rows_wrap(self):
        session = self.layer()
        session.press("kb:s")
        first = session.press("kb:downArrow")[-1]
        second = session.press("kb:downArrow")[-1]
        self.assertNotEqual(first, second, "the row list must wrap, not stop")

    def test_unload_does_not_block_nvda(self):
        house = default_household(latency=1.5)
        session = self.session(household=house)
        session.press("kb:NVDA+shift+control+s", settle=False)
        time.sleep(0.3)
        start = time.monotonic()
        session.plugin.terminate()
        self.assertLess(time.monotonic() - start, 2.0,
                        "unload must not wait out a slow speaker request")

    def test_alarm_dialog_stays_quiet_when_fields_appear(self):
        """Showing a field is not worth announcing; NVDA reads it on focus."""
        session = self.layer()
        session.press("kb:a")

        def inspect(dialog):
            repeat = dialog.focus_on("Repeat")
            while repeat.GetStringSelection() != "Selected days":
                dialog.press("down")
            self.assertIn("Days", [c.GetName() for c in dialog.tab_order()])
            return False

        session.on_dialog(inspect)
        before = len(session.speech)
        session.press("kb:enter")
        self.assertEqual(session.speech[before:], [],
                         f"the dialog spoke: {session.speech[before:]}")


class Cost(EmulatorTest):
    """Round-trip budgets, so a regression in responsiveness is visible."""

    def test_browsing_settings_costs_nothing(self):
        session = self.layer()
        session.press("kb:control+s")
        session.count_calls()
        session.press("kb:downArrow", "kb:downArrow", "kb:upArrow")
        self.assertEqual(session.count_calls(reset=False), 0,
                         "moving between settings must read from the snapshot")

    def test_changing_one_setting_writes_and_confirms(self):
        session = self.layer()
        session.press("kb:control+s")
        session.count_calls()
        session.press("kb:rightArrow")
        self.assertLessEqual(session.count_calls(reset=False), 2)

    def test_a_folder_costs_one_request_per_batch(self):
        import os
        import tempfile
        house = default_household()
        session = self.layer(household=house)
        folder = tempfile.mkdtemp(prefix="sonos-emu-")
        for index in range(32):
            with open(os.path.join(folder, f"track {index:02d}.mp3"), "wb") as handle:
                handle.write(bytes([0xFF, 0xFB]) + bytes(400))

        def choose(dialog):
            dialog.result = {"path": folder, "recursive": False}
            return True

        session.on_dialog(choose)
        session.count_calls()
        session.press("kb:control+f")
        adds = [c for c in session.household.calls if c == "AddMultipleURIsToQueue"]
        self.assertEqual(len(adds), 2, "32 tracks should take two batched requests")


if __name__ == "__main__":
    unittest.main(verbosity=2)
