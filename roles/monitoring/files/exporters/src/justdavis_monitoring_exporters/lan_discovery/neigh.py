"""The kernel's IPv4 neighbour (ARP) table as a producer for the device address inventory.

Why this source: a neighbour entry is the device itself answering for the address on this segment, and
ping_exporter's one ping a second to every target keeps the entries of the devices that matter
confirmed, so this is the source that stays fresh when the router scrape is down and mDNS is quiet.
Entries are read with a netlink `RTM_GETNEIGH` dump, which needs no privilege (dump requests are open
to any process; verified 2026-10-31 inside the hardened host-network container), and which, unlike
`/proc/net/arp`, carries each entry's state and how long ago it was last confirmed.

What is kept: entries on the LAN interface, inside the LAN subnet, with a hardware address and in a
state that means the kernel has heard from the device (REACHABLE, STALE, DELAY, PROBE). INCOMPLETE and
FAILED entries have no address to report; NOARP and PERMANENT are not devices. The sighting time is the
most recent of the entry's `confirmed` and `updated` ages: an entry created from a gratuitous ARP (the
kernel does that with `arp_accept=2`, so a device announcing a new lease lands here without anyone
asking) starts with `confirmed` already in the past. Ages are in USER_HZ ticks (100 a second here).

Provoking: the table only learns an address once someone talks to it. For a tracked device no source
has seen lately, `provoke()` sends one empty UDP datagram to each of its known addresses, which makes
the kernel ARP for them; a device that is there answers and the next read sees it. Nothing sweeps the
whole subnet: that would cost hundreds of broadcast frames a minute on the 2.4 GHz radios that are
already airtime-bound, and wake every dozing device.
"""

import ipaddress
import logging
import os
import socket
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import ifaddr

from justdavis_monitoring_exporters.common.device_addresses import Sighting
from justdavis_monitoring_exporters.common.settings import Mac, canonical_mac

log = logging.getLogger(__name__)

NLM_F_REQUEST = 0x1
NLM_F_DUMP = 0x300
NLMSG_ERROR = 0x2
NLMSG_DONE = 0x3
RTM_NEWNEIGH = 28
RTM_GETNEIGH = 30
NDA_DST = 1
NDA_LLADDR = 2
NDA_CACHEINFO = 3

NUD_INCOMPLETE = 0x01
NUD_REACHABLE = 0x02
NUD_STALE = 0x04
NUD_DELAY = 0x08
NUD_PROBE = 0x10
NUD_FAILED = 0x20
NUD_NOARP = 0x40
NUD_PERMANENT = 0x80
# States in which the kernel has heard from the device at this address.
_HEARD_FROM = NUD_REACHABLE | NUD_STALE | NUD_DELAY | NUD_PROBE
_STATE_NAMES = {
    NUD_INCOMPLETE: "INCOMPLETE",
    NUD_REACHABLE: "REACHABLE",
    NUD_STALE: "STALE",
    NUD_DELAY: "DELAY",
    NUD_PROBE: "PROBE",
    NUD_FAILED: "FAILED",
    NUD_NOARP: "NOARP",
    NUD_PERMANENT: "PERMANENT",
}

_NLMSGHDR = struct.Struct("IHHII")
_NDMSG = struct.Struct("BxxxiHBB")
_RTATTR = struct.Struct("HH")
_CACHEINFO = struct.Struct("IIII")
_RECV_BUFFER = 65536
# Linux-only socket constants, named here so the module imports (and its pure parts test) anywhere.
_AF_NETLINK: int = getattr(socket, "AF_NETLINK", 16)
_NETLINK_ROUTE: int = getattr(socket, "NETLINK_ROUTE", 0)
# The port the provoking datagram goes to: "discard", whose only effect is an ICMP reply we ignore.
_DISCARD_PORT = 9


@dataclass(frozen=True, slots=True)
class Neighbour:
    ifindex: int
    address: str
    mac: Mac | None
    state: int
    confirmed_age_seconds: float
    updated_age_seconds: float

    @property
    def state_name(self) -> str:
        return _STATE_NAMES.get(self.state, hex(self.state))


def parse_neighbour_dump(data: bytes, *, ticks_per_second: int) -> tuple[Neighbour, ...]:
    """The IPv4 neighbours in one netlink receive buffer (a sequence of `RTM_NEWNEIGH` messages)."""
    neighbours: list[Neighbour] = []
    offset = 0
    while offset + _NLMSGHDR.size <= len(data):
        length, kind, _flags, _seq, _pid = _NLMSGHDR.unpack_from(data, offset)
        if length < _NLMSGHDR.size or offset + length > len(data):
            break
        if kind == NLMSG_DONE:
            break
        if kind == RTM_NEWNEIGH and length >= _NLMSGHDR.size + _NDMSG.size:
            family, ifindex, state, _ndm_flags, _ndm_type = _NDMSG.unpack_from(data, offset + _NLMSGHDR.size)
            if family == socket.AF_INET:
                neighbour = _parse_attributes(
                    data,
                    offset + _NLMSGHDR.size + _NDMSG.size,
                    offset + length,
                    ifindex,
                    state,
                    ticks_per_second,
                )
                if neighbour is not None:
                    neighbours.append(neighbour)
        offset += (length + 3) & ~3
    return tuple(neighbours)


def _parse_attributes(
    data: bytes, start: int, end: int, ifindex: int, state: int, ticks_per_second: int
) -> Neighbour | None:
    address: str | None = None
    mac: Mac | None = None
    confirmed = updated = 0.0
    offset = start
    while offset + _RTATTR.size <= end:
        attr_length, attr_type = _RTATTR.unpack_from(data, offset)
        if attr_length < _RTATTR.size or offset + attr_length > end:
            break
        payload = data[offset + _RTATTR.size : offset + attr_length]
        if attr_type == NDA_DST and len(payload) == 4:
            address = str(ipaddress.IPv4Address(payload))
        elif attr_type == NDA_LLADDR and len(payload) == 6:
            mac = canonical_mac(":".join(f"{b:02x}" for b in payload))
        elif attr_type == NDA_CACHEINFO and len(payload) == _CACHEINFO.size:
            confirmed_ticks, _used, updated_ticks, _refcnt = _CACHEINFO.unpack(payload)
            confirmed = confirmed_ticks / ticks_per_second
            updated = updated_ticks / ticks_per_second
        offset += (attr_length + 3) & ~3
    if address is None:
        return None
    return Neighbour(ifindex, address, mac, state, confirmed, updated)


def read_neighbours(*, ticks_per_second: int | None = None) -> tuple[Neighbour, ...]:
    """Dump the kernel's IPv4 neighbour table over netlink (no privilege needed)."""
    ticks = ticks_per_second if ticks_per_second is not None else os.sysconf("SC_CLK_TCK")
    neighbours: list[Neighbour] = []
    with socket.socket(_AF_NETLINK, socket.SOCK_RAW, _NETLINK_ROUTE) as sock:
        sock.bind((0, 0))
        sock.settimeout(5.0)
        request = _NLMSGHDR.pack(
            _NLMSGHDR.size + _NDMSG.size, RTM_GETNEIGH, NLM_F_REQUEST | NLM_F_DUMP, 1, 0
        ) + _NDMSG.pack(socket.AF_INET, 0, 0, 0, 0)
        sock.send(request)
        while True:
            data = sock.recv(_RECV_BUFFER)
            neighbours.extend(parse_neighbour_dump(data, ticks_per_second=ticks))
            if _dump_finished(data):
                break
    return tuple(neighbours)


def _dump_finished(data: bytes) -> bool:
    offset = 0
    while offset + _NLMSGHDR.size <= len(data):
        length, kind, _flags, _seq, _pid = _NLMSGHDR.unpack_from(data, offset)
        if kind in (NLMSG_DONE, NLMSG_ERROR):
            return True
        if length < _NLMSGHDR.size:
            return True
        offset += (length + 3) & ~3
    return False


def lan_interface_index(lan_ip: str) -> int | None:
    """The index of the interface that holds `lan_ip`, so neighbours on other interfaces (the WAN
    bridge, Docker bridges, the VPN) are left out."""
    for adapter in ifaddr.get_adapters():
        if any(ip.is_IPv4 and str(ip.ip) == lan_ip for ip in adapter.ips):
            return adapter.index
    return None


def neighbour_sightings(
    neighbours: Iterable[Neighbour],
    *,
    ifindex: int,
    subnet: ipaddress.IPv4Network,
    now: float,
) -> tuple[Sighting, ...]:
    sightings: list[Sighting] = []
    for neighbour in neighbours:
        if neighbour.ifindex != ifindex or neighbour.mac is None or not neighbour.state & _HEARD_FROM:
            continue
        if ipaddress.IPv4Address(neighbour.address) not in subnet:
            continue
        age = min(neighbour.confirmed_age_seconds, neighbour.updated_age_seconds)
        sightings.append(
            Sighting(
                address=neighbour.address,
                observed_at=now - age,
                mac=neighbour.mac,
                extra={"state": neighbour.state_name},
            )
        )
    return tuple(sightings)


def provoke(
    lan_ip: str,
    addresses: Iterable[str],
    *,
    send: Callable[[str], None] | None = None,
) -> int:
    """Make the kernel ARP for each address by sending it one empty datagram; returns how many were
    sent. An address that cannot be sent to (an earlier failed resolution surfaces as EHOSTUNREACH)
    is skipped, not fatal."""
    sent = 0
    sock: socket.socket | None = None
    try:
        if send is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            sock.bind((lan_ip, 0))

            def send_one(address: str) -> None:
                assert sock is not None
                sock.sendto(b"", (address, _DISCARD_PORT))

            send = send_one
        for address in addresses:
            if address == lan_ip:
                continue
            try:
                send(address)
                sent += 1
            except OSError as exc:
                log.debug("could not provoke %s: %s", address, exc)
    finally:
        if sock is not None:
            sock.close()
    return sent
