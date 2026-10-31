"""Tests for the router feed as a producer of device address sightings."""

from dataclasses import replace
from pathlib import Path

from justdavis_monitoring_exporters.amplifi.parser import parse_info_async
from justdavis_monitoring_exporters.amplifi.sightings import router_sightings
from justdavis_monitoring_exporters.common.settings import Mac

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = parse_info_async((FIXTURES / "amplifi_info_async.json").read_bytes())
NOW = 1_000_000.0


def test_router_mesh_points_wifi_clients_and_wired_devices_are_sighted() -> None:
    by_mac = {s.mac: s for s in router_sightings(SNAPSHOT, now=NOW)}
    router = by_mac[Mac("02:00:00:00:00:01")]
    assert (router.address, router.role, router.name) == ("192.0.2.1", "router", "test-router")
    kitchen = by_mac[Mac("02:00:00:00:00:02")]
    assert (kitchen.address, kitchen.role, kitchen.name) == ("192.0.2.239", "mesh_point", "Kitchen")
    speaker_a = by_mac[Mac("02:00:00:00:00:10")]
    assert (speaker_a.address, speaker_a.role, speaker_a.observed_at) == ("192.0.2.110", "client", NOW)
    assert speaker_a.extra["connection"] == "wireless"
    assert set(speaker_a.extra) == {"connection", "ap", "band"}
    server = by_mac[Mac("02:00:00:00:00:30")]
    assert (server.address, server.name, server.extra) == ("192.0.2.2", "server", {"connection": "ethernet"})
    assert all(s.observed_at == NOW for s in by_mac.values())


def test_backhaul_links_clients_without_an_address_and_bare_leases_are_not_sightings() -> None:
    macs = {s.mac for s in router_sightings(SNAPSHOT, now=NOW)}
    assert Mac("02:00:00:00:00:f2") not in macs
    without_ip = replace(SNAPSHOT, clients=tuple(replace(c, ip=None) for c in SNAPSHOT.clients))
    assert Mac("02:00:00:00:00:10") not in {s.mac for s in router_sightings(without_ip, now=NOW)}
    # A device in the directory that is neither associated nor on a wired port is only a remembered
    # lease, so it is not sighted.
    unplugged = replace(SNAPSHOT, wired=frozenset())
    assert Mac("02:00:00:00:00:30") not in {s.mac for s in router_sightings(unplugged, now=NOW)}


def test_offline_mesh_point_is_not_sighted() -> None:
    offline = replace(
        SNAPSHOT,
        mesh_points=tuple(
            replace(mp, online=False) if mp.mac == "02:00:00:00:00:03" else mp for mp in SNAPSHOT.mesh_points
        ),
    )
    macs = {s.mac for s in router_sightings(offline, now=NOW)}
    assert Mac("02:00:00:00:00:03") not in macs
    assert Mac("02:00:00:00:00:02") in macs
