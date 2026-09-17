"""Transfer behavior and cancellation without touching real speakers."""
import unittest
from test_sonos_backend import Player, Group, backend

from support import load

module = load("transfer")


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.source = Player("s", "Living Room")
        self.target = Player("t", "Move 2")
        self.target.state = "PAUSED_PLAYBACK"
        self.source.volume = 31
        self.target.volume = 13
        Group(self.source)
        Group(self.target)
        self.controller = backend.Controller(lambda **kw: [self.source, self.target])
        self.cancelled = False
        self.calls = []
        def join(source):
            self.calls.append("join")
            Group(source, self.target)
        def source_unjoin():
            self.calls.append("source unjoin")
            Group(self.source)
            Group(self.target)
        def target_unjoin():
            self.calls.append("target unjoin")
            Group(self.source)
            Group(self.target)
        self.target.join = join
        self.source.unjoin = source_unjoin
        self.target.unjoin = target_unjoin

    def transfer(self):
        return module.Transfer(self.controller, lambda: self.cancelled)

    def test_open_reuses_discovered_rooms(self):
        self.controller.refresh()
        self.controller.refresh = lambda: self.fail("Must not repeat SSDP discovery")
        self.transfer()

    def test_success(self):
        transfer = self.transfer()
        self.assertIn("Move 2", transfer.describe())
        self.assertIn("transferred", transfer.commit())
        self.assertEqual(self.calls, ["join", "source unjoin"])
        self.assertEqual(self.controller.selected, "t")
        self.assertEqual(self.target.volume, 31)

    def test_cancel_before_commit(self):
        transfer = self.transfer()
        self.cancelled = True
        self.assertIn("cancelled", transfer.commit())
        self.assertEqual(self.calls, [])

    def test_cancel_after_join_rolls_back(self):
        transfer = self.transfer()
        join = self.target.join
        def cancel_after_join(source):
            join(source)
            self.cancelled = True
        self.target.join = cancel_after_join
        self.assertIn("cancelled", transfer.commit())
        self.assertEqual(self.calls, ["join", "target unjoin"])
        self.assertEqual(self.target.volume, 13)

    def test_destination_started_playing(self):
        transfer = self.transfer()
        self.target.state = "PLAYING"
        with self.assertRaisesRegex(RuntimeError, "Rooms changed"):
            transfer.commit()
        self.assertEqual(self.calls, [])

    def test_failed_detach_rolls_back(self):
        transfer = self.transfer()
        def fail():
            raise OSError("offline")
        self.source.unjoin = fail
        with self.assertRaisesRegex(RuntimeError, "could not be confirmed"):
            transfer.commit()
        self.assertEqual(self.calls, ["join", "target unjoin"])

    def test_no_free_destination(self):
        self.target.state = "PLAYING"
        with self.assertRaisesRegex(RuntimeError, "No free"):
            self.transfer()


if __name__ == "__main__":
    unittest.main()
