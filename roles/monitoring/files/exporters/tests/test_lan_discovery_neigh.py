"""Tests for the neighbour-table source: parsing a netlink dump, choosing sightings, provoking."""

import ipaddress
import socket
import struct

from justdavis_monitoring_exporters.common.settings import Mac
from justdavis_monitoring_exporters.lan_discovery.neigh import (
    NDA_CACHEINFO,
    NDA_DST,
    NDA_LLADDR,
    NLMSG_DONE,
    NUD_FAILED,
    NUD_INCOMPLETE,
    NUD_NOARP,
    NUD_REACHABLE,
    NUD_STALE,
    RTM_NEWNEIGH,
    Neighbour,
    neighbour_sightings,
    parse_neighbour_dump,
    provoke,
)

LAN = 5
SUBNET = ipaddress.IPv4Network("192.0.2.0/24")
NOW = 1_000_000.0


def attr(kind: int, payload: bytes) -> bytes:
    length = 4 + len(payload)
    return struct.pack("HH", length, kind) + payload + b"\0" * ((-length) % 4)


def neigh_msg(
    ifindex: int,
    state: int,
    address: str | None,
    mac: str | None,
    *,
    confirmed_ticks: int = 0,
    updated_ticks: int = 0,
) -> bytes:
    body = struct.pack("BxxxiHBB", socket.AF_INET, ifindex, state, 0, 0)
    if address is not None:
        body += attr(NDA_DST, socket.inet_aton(address))
    if mac is not None:
        body += attr(NDA_LLADDR, bytes.fromhex(mac.replace(":", "")))
    body += attr(NDA_CACHEINFO, struct.pack("IIII", confirmed_ticks, 0, updated_ticks, 1))
    length = 16 + len(body)
    return struct.pack("IHHII", length, RTM_NEWNEIGH, 0, 1, 0) + body + b"\0" * ((-length) % 4)


def done() -> bytes:
    return struct.pack("IHHII", 20, NLMSG_DONE, 0, 1, 0) + b"\0\0\0\0"


def test_parse_reads_address_mac_state_and_ages_in_ticks() -> None:
    dump = (
        neigh_msg(
            LAN,
            NUD_REACHABLE,
            "192.0.2.110",
            "02:00:00:00:00:10",
            confirmed_ticks=650,
            updated_ticks=3_519_110,
        )
        + done()
    )
    (neighbour,) = parse_neighbour_dump(dump, ticks_per_second=100)
    assert neighbour == Neighbour(LAN, "192.0.2.110", Mac("02:00:00:00:00:10"), NUD_REACHABLE, 6.5, 35191.1)
    assert neighbour.state_name == "REACHABLE"


def test_parse_skips_entries_without_an_address_and_stops_at_done() -> None:
    dump = (
        neigh_msg(LAN, NUD_FAILED, "192.0.2.60", None)
        + neigh_msg(LAN, NUD_INCOMPLETE, None, None)
        + done()
        + neigh_msg(LAN, NUD_REACHABLE, "192.0.2.99", "02:00:00:00:00:99")
    )
    neighbours = parse_neighbour_dump(dump, ticks_per_second=100)
    assert [(n.address, n.mac, n.state) for n in neighbours] == [("192.0.2.60", None, NUD_FAILED)]


def test_sightings_keep_heard_from_entries_on_the_lan_interface_inside_the_subnet() -> None:
    neighbours = (
        Neighbour(LAN, "192.0.2.110", Mac("02:00:00:00:00:10"), NUD_REACHABLE, 6.5, 35191.1),
        Neighbour(LAN, "192.0.2.111", Mac("02:00:00:00:00:11"), NUD_STALE, 2387.2, 2370.3),
        # A gratuitous-ARP-created entry: confirmed long ago, updated just now.
        Neighbour(LAN, "192.0.2.112", Mac("02:00:00:00:00:12"), NUD_STALE, 13060.1, 0.9),
        Neighbour(LAN, "192.0.2.60", None, NUD_FAILED, 13060.1, 0.9),
        Neighbour(LAN, "192.0.2.61", Mac("02:00:00:00:00:61"), NUD_NOARP, 0.0, 0.0),
        Neighbour(165, "172.28.0.6", Mac("02:00:00:00:00:a6"), NUD_REACHABLE, 1.0, 1.0),
        Neighbour(LAN, "10.1.10.1", Mac("02:00:00:00:00:b1"), NUD_REACHABLE, 1.0, 1.0),
    )
    sightings = neighbour_sightings(neighbours, ifindex=LAN, subnet=SUBNET, now=NOW)
    assert [(s.mac, s.address, s.observed_at, s.extra["state"]) for s in sightings] == [
        (Mac("02:00:00:00:00:10"), "192.0.2.110", NOW - 6.5, "REACHABLE"),
        (Mac("02:00:00:00:00:11"), "192.0.2.111", NOW - 2370.3, "STALE"),
        (Mac("02:00:00:00:00:12"), "192.0.2.112", NOW - 0.9, "STALE"),
    ]


def test_provoke_sends_one_datagram_per_address_skipping_itself_and_surviving_an_error() -> None:
    sent: list[str] = []

    def send(address: str) -> None:
        if address == "192.0.2.13":
            raise OSError("No route to host")
        sent.append(address)

    count = provoke("192.0.2.2", ["192.0.2.2", "192.0.2.12", "192.0.2.13", "192.0.2.14"], send=send)
    assert (count, sent) == (2, ["192.0.2.12", "192.0.2.14"])
