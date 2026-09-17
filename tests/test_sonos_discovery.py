"""Validated fast reconnect and full-discovery fallback."""
import types
import unittest
from unittest.mock import patch, Mock

from support import load

discovery = load("discovery")

class DiscoveryTests(unittest.TestCase):
    def test_fresh_topology_includes_new_rooms_and_skips_offline(self):
        def room(ip):
            return types.SimpleNamespace(ip_address=ip, is_visible=True, player_name=ip, uid=ip)
        seed, new, offline = [room('192.168.0.' + str(n)) for n in (2, 3, 4)]
        seed.zone_group_state = types.SimpleNamespace(clear_cache=Mock())
        seed.all_zones = [seed, new, offline]
        soco = types.SimpleNamespace(SoCo=lambda ip: seed)
        saved = Mock()
        finder = discovery.RoomDiscovery(soco, [seed.ip_address], saved)
        with patch.object(discovery, 'reachable', side_effect=lambda ip: ip != offline.ip_address), patch.object(discovery, 'discover_rooms') as full:
            self.assertEqual(finder(), [seed, new])
            full.assert_not_called()
        seed.zone_group_state.clear_cache.assert_called_once()
        saved.assert_called_once_with([seed.ip_address, new.ip_address])

    def test_dead_addresses_fall_back_with_original_timeout(self):
        finder = discovery.RoomDiscovery(None, ['192.168.0.2'])
        with patch.object(discovery, 'reachable', return_value=False), patch.object(discovery, 'discover_rooms', return_value=[]) as full:
            self.assertEqual(finder(), [])
            full.assert_called_once_with(None, 4)

    def test_topology_error_falls_back(self):
        finder = discovery.RoomDiscovery(None, ['192.168.0.2'])
        with patch.object(finder, '_known_rooms', side_effect=TimeoutError()), patch.object(discovery, 'discover_rooms', return_value=[]) as full:
            finder()
            full.assert_called_once()

    def test_saved_addresses_are_used_when_they_answer(self):
        finder = discovery.RoomDiscovery(None, ['192.168.0.2'])
        rooms = [types.SimpleNamespace(ip_address='192.168.0.2')]
        with patch.object(finder, '_known_rooms', return_value=rooms) as fast, patch.object(discovery, 'discover_rooms') as full:
            self.assertEqual(finder(), rooms)
            fast.assert_called_once()
            full.assert_not_called()

    def test_invalid_saved_addresses_are_not_probed(self):
        finder = discovery.RoomDiscovery(None, ['example.com', '', '127.0.0.1', '239.255.255.250', '192.168.0.2'])
        self.assertEqual(finder.addresses, ['192.168.0.2'])

