"""Collect SSDP replies before asking reachable players for their topology."""

import concurrent.futures
import ipaddress
import logging
import select
import socket
import time

from . import vendors
from .i18n import _

log = logging.getLogger(__name__)

#: Saved addresses are hints only, and the list stays small on purpose.
MAX_ADDRESSES = 32


def reachable(ip):
    try:
        with socket.create_connection((ip, 1400), timeout=1):
            return True
    except OSError:
        return False


def _describe(player):
    """Resolve the properties needed for sorting, so one bad player cannot abort it."""
    player.player_name
    player.uid
    return player


def discover_rooms(soco, timeout=4):
    ifaddr = vendors.ifaddr()

    request = (b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n'
               b'MAN: "ssdp:discover"\r\nMX: 1\r\n'
               b'ST: urn:schemas-upnp-org:device:ZonePlayer:1\r\n\r\n')
    sockets = []
    addresses = set()
    try:
        for adapter in ifaddr.get_adapters():
            for address in adapter.ips:
                if not isinstance(address.ip, str) or address.ip.startswith(("127.", "169.254.")):
                    continue
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    sock.bind((address.ip, 0))
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address.ip))
                    sock.sendto(request, ("239.255.255.250", 1900))
                    sock.sendto(request, ("239.255.255.250", 1900))
                    sockets.append(sock)
                except OSError:
                    sock.close()
        started = time.monotonic()
        deadline = started + timeout
        response_deadline = None
        while sockets and time.monotonic() < (response_deadline or deadline):
            wait_until = response_deadline or deadline
            # Not ``_``: that name is the translation lookup in this module.
            ready, _writable, _failed = select.select(
                sockets, [], [], max(0, wait_until - time.monotonic()))
            for sock in ready:
                try:
                    data, source = sock.recvfrom(8192)
                except OSError:
                    # Windows reports an earlier ICMP rejection as a reset on
                    # the next read.  One unreachable address must not end the
                    # whole scan.
                    continue
                if b"sonos" in data.lower():
                    addresses.add(source[0])
                    # MX is one second. Once a player answers, allow the full MX
                    # window plus a short late-response grace instead of always
                    # waiting for the four-second failure timeout.
                    now = time.monotonic()
                    response_deadline = min(deadline, max(started + 1.5, now + 0.25))
    finally:
        for sock in sockets:
            sock.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        available = [ip for ip, ok in zip(sorted(addresses), pool.map(reachable, sorted(addresses))) if ok]
    rooms = _resolve(soco.SoCo(ip) for ip in available)
    if addresses and not rooms:
        raise RuntimeError(
            _("Sonos speakers were found, but local control on port 1400 is unavailable."))
    return rooms


def _resolve(players):
    """Read each player's identity in parallel, skipping the ones that fail."""
    rooms = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for player, future in [(p, pool.submit(_describe, p)) for p in players]:
            try:
                if future.result().is_visible:
                    rooms.append(player)
            except Exception:
                log.debug("Skipping unreachable Sonos player", exc_info=True)
    return rooms

class RoomDiscovery:
    """Use saved addresses only as seeds for fresh, validated topology."""

    def __init__(self, soco, addresses=(), remember=lambda addresses: None):
        self.soco = soco
        self.remember = remember
        self.addresses = []
        for value in addresses:
            try:
                address = ipaddress.IPv4Address(value)
                if not address.is_loopback and not address.is_multicast and not address.is_unspecified:
                    self.addresses.append(str(address))
            except ValueError:
                continue
        self.addresses = list(dict.fromkeys(self.addresses))[:MAX_ADDRESSES]

    def __call__(self, timeout=4):
        rooms = []
        if self.addresses:
            try:
                rooms = self._known_rooms()
            except Exception:
                log.debug("Fast reconnect failed; running SSDP", exc_info=True)
        if not rooms:
            rooms = discover_rooms(self.soco, timeout)
        if rooms:
            self.addresses = list(dict.fromkeys(p.ip_address for p in rooms))[:MAX_ADDRESSES]
            self.remember(self.addresses[:])
        return rooms

    def _known_rooms(self):
        # Probe in parallel so an offline portable does not add a timeout per room.
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            available = [ip for ip, ok in zip(self.addresses, pool.map(reachable, self.addresses)) if ok]
        if not available:
            return []
        # A live port alone is insufficient: require a fresh Sonos topology.
        seed = self.soco.SoCo(available[0])
        seed.zone_group_state.clear_cache()
        zones = list(seed.all_zones)
        if not zones or not any(p.ip_address == seed.ip_address for p in zones):
            return []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            live = [p for p, ok in zip(zones, pool.map(reachable, [p.ip_address for p in zones]))
                    if ok]
        return _resolve(live)
