"""Group membership selection and coordinator removal ordering."""
import unittest
from test_sonos_backend import Player, Group, backend

from support import load

module = load("grouping")


class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b, self.c = [Player(uid, uid) for uid in ("A", "B", "C")]
        self.rooms = [self.a, self.b, self.c]
        self.calls = []
        self.cancelled = False
        for room in self.rooms:
            Group(room)
            room.join = lambda source, room=room: self.join(room, source)
            room.unjoin = lambda room=room: self.unjoin(room)
        self.controller = backend.Controller(lambda **kw: self.rooms, selected="A")

    def join(self, room, source):
        self.calls.append(("join", room.uid))
        Group(source, *(p for p in source.group.members if p is not source), room)

    def unjoin(self, room):
        self.calls.append(("unjoin", room.uid))
        remaining = [p for p in room.group.members if p is not room]
        if remaining:
            Group(*remaining)
        Group(room)

    def session(self):
        return module.Grouping(self.controller, lambda: self.cancelled)

    def test_open_reuses_discovered_rooms(self):
        self.controller.refresh()
        self.controller.refresh = lambda: self.fail("Must not repeat SSDP discovery")
        self.session()

    def test_selection_changes_nothing_until_commit(self):
        session = self.session()
        self.assertEqual(session.selected, {"A"})
        session.move(1)
        session.toggle()
        self.assertEqual(self.calls, [])
        self.assertIn("Playing rooms", session.commit())
        self.assertEqual(self.calls, [("join", "B")])

    def test_ungroup_keep_noncoordinator(self):
        Group(self.a, self.b, self.c)
        session = self.session()
        session.selected = {"B"}
        session.commit()
        self.assertEqual(self.calls, [("unjoin", "C"), ("unjoin", "A")])
        self.assertEqual(self.controller.selected, "B")

    def test_new_destination_before_source_leaves(self):
        session = self.session()
        session.selected = {"C"}
        session.commit()
        self.assertEqual(self.calls, [("join", "C"), ("unjoin", "A")])

    def test_empty_selection_rejected(self):
        session = self.session()
        session.selected.clear()
        with self.assertRaisesRegex(RuntimeError, "at least one"):
            session.commit()
        self.assertEqual(self.calls, [])

    def test_cancel_before_commit(self):
        session = self.session()
        session.selected.add("B")
        self.cancelled = True
        self.assertIn("cancelled", session.commit())
        self.assertEqual(self.calls, [])

    def test_cancel_between_mutations(self):
        session = self.session()
        session.selected = {"B", "C"}
        def join_then_cancel(source):
            self.join(self.b, source)
            self.cancelled = True
        self.b.join = join_then_cancel
        self.assertIn("Some changes", session.commit())
        self.assertEqual(self.calls, [("join", "B")])

    def test_failure_explains_partial_state(self):
        session = self.session()
        session.selected.add("B")
        def fail(source):
            raise OSError("offline")
        self.b.join = fail
        with self.assertRaisesRegex(RuntimeError, "Some changes"):
            session.commit()

    def test_delayed_topology_is_confirmed_without_repeating_join(self):
        from unittest.mock import patch
        session = self.session()
        session.selected.add('B')
        pending = []
        self.b.join = lambda source: pending.append(source)
        polls = []
        def refresh(player):
            polls.append(player)
            if pending and len(polls) >= 3:
                self.join(self.b, pending.pop())
        with patch.object(session, 'refresh', side_effect=refresh), patch.object(module.time, 'sleep'):
            self.assertIn('Playing rooms', session.commit())
        self.assertEqual(self.calls, [('join', 'B')])

    def test_timeout_after_applied_join_is_success(self):
        session = self.session()
        session.selected.add('B')
        def join(source):
            self.join(self.b, source)
            raise TimeoutError()
        self.b.join = join
        self.assertIn('Playing rooms', session.commit())
        self.assertEqual(self.calls, [('join', 'B')])

    def test_unconfirmed_change_is_not_success(self):
        from unittest.mock import patch
        session = self.session()
        session.selected.add('B')
        self.b.join = lambda source: None
        with patch.object(module.time, 'sleep'), patch.object(module.time, 'monotonic', side_effect=range(30)):
            with self.assertRaisesRegex(RuntimeError, 'Some changes'):
                session.commit()
