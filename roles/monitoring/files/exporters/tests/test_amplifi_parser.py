"""Tests for the AmpliFi `info-async.php` JSON parser."""

import json
from pathlib import Path

import pytest

from justdavis_monitoring_exporters.amplifi.parser import parse_info_async
from justdavis_monitoring_exporters.common.errors import ParseError
from justdavis_monitoring_exporters.common.settings import Mac

FIXTURES = Path(__file__).parent / "fixtures"

ROUTER_MAC = Mac("02:00:00:00:00:01")
KITCHEN_MP_MAC = Mac("02:00:00:00:00:02")
SPEAKER_A_MAC = Mac("02:00:00:00:00:10")
SPEAKER_B_MAC = Mac("02:00:00:00:00:11")
TABLET_MAC = Mac("02:00:00:00:00:20")
BACKHAUL_MAC = Mac("02:00:00:00:00:f2")


@pytest.fixture(scope="module")
def info_async_bytes() -> bytes:
    return (FIXTURES / "amplifi_info_async.json").read_bytes()


def test_parses_router(info_async_bytes: bytes) -> None:
    snapshot = parse_info_async(info_async_bytes)
    assert snapshot.router.mac == ROUTER_MAC
    assert snapshot.router.name == "test-router"
    assert snapshot.router.platform == "AFi-R-HD"
    assert snapshot.router.uptime_seconds == 33652


def test_parses_mesh_points(info_async_bytes: bytes) -> None:
    snapshot = parse_info_async(info_async_bytes)
    by_mac = {mp.mac: mp for mp in snapshot.mesh_points}
    assert set(by_mac) == {KITCHEN_MP_MAC, "02:00:00:00:00:03"}
    kitchen = by_mac[KITCHEN_MP_MAC]
    assert kitchen.name == "Kitchen"
    assert kitchen.ip == "192.0.2.239"
    assert kitchen.backhaul_band == "2.4 GHz"
    assert kitchen.rssi_min_dbm == -44
    assert kitchen.uptime_seconds == 2008337
    assert kitchen.platform == "AFi-P-HD"


def test_mesh_point_display_name_drops_model_suffix(info_async_bytes: bytes) -> None:
    snapshot = parse_info_async(info_async_bytes)
    living_room = next(mp for mp in snapshot.mesh_points if mp.mac == "02:00:00:00:00:03")
    assert living_room.name == "Living Room"


def test_parses_user_network_clients(info_async_bytes: bytes) -> None:
    snapshot = parse_info_async(info_async_bytes)
    by_mac = {c.mac: c for c in snapshot.clients}
    speaker_a = by_mac[SPEAKER_A_MAC]
    assert speaker_a.ap_mac == ROUTER_MAC
    assert speaker_a.band == "2.4 GHz"
    assert speaker_a.network == "User network"
    assert speaker_a.ip == "192.0.2.110"
    assert speaker_a.hostname == "Speaker-A"
    assert speaker_a.mode == "802.11n"
    assert speaker_a.signal_quality == 74
    assert speaker_a.happiness_score == 75
    assert speaker_a.rx_bitrate_kbps == 52000
    assert speaker_a.tx_bitrate_kbps == 11000
    assert speaker_a.rx_bytes == 4294967200
    assert speaker_a.tx_bytes == 56870999
    assert speaker_a.inactive_seconds == 30


def test_client_on_mesh_point_records_that_ap(info_async_bytes: bytes) -> None:
    snapshot = parse_info_async(info_async_bytes)
    speaker_b = next(c for c in snapshot.clients if c.mac == SPEAKER_B_MAC)
    assert speaker_b.ap_mac == KITCHEN_MP_MAC
    assert speaker_b.band == "5 GHz"


def test_backhaul_links_are_kept_but_flagged(info_async_bytes: bytes) -> None:
    snapshot = parse_info_async(info_async_bytes)
    backhaul = next(c for c in snapshot.clients if c.mac == BACKHAUL_MAC)
    assert backhaul.network == "Internal network"
    assert backhaul.ip is None
    assert backhaul.hostname is None
    assert backhaul.mode is None


def test_mac_addresses_are_normalised_to_lowercase(info_async_bytes: bytes) -> None:
    snapshot = parse_info_async(info_async_bytes)
    assert all(c.mac == c.mac.lower() for c in snapshot.clients)


def test_parses_wan_port(info_async_bytes: bytes) -> None:
    snapshot = parse_info_async(info_async_bytes)
    wan = snapshot.wan_port
    assert wan.link is True
    assert wan.link_speed_mbps == 1000
    assert wan.rx_bitrate_kbps == 21931
    assert wan.tx_bitrate_kbps == 542


def test_parses_bonjour_services(info_async_bytes: bytes) -> None:
    snapshot = parse_info_async(info_async_bytes)
    assert snapshot.bonjour[SPEAKER_A_MAC] == frozenset(
        {"_airplay._tcp.local", "_companion-link._tcp.local", "_raop._tcp.local"}
    )
    assert "02:00:00:00:00:30" not in snapshot.bonjour


def test_wrong_element_count_raises_parse_error(info_async_bytes: bytes) -> None:
    data = json.loads(info_async_bytes)
    with pytest.raises(ParseError):
        parse_info_async(json.dumps(data[:3]).encode())


def test_non_json_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_info_async(b"<html>login</html>")


def test_wrong_field_type_raises_parse_error(info_async_bytes: bytes) -> None:
    data = json.loads(info_async_bytes)
    data[0][ROUTER_MAC]["uptime"] = "soon"
    with pytest.raises(ParseError) as excinfo:
        parse_info_async(json.dumps(data).encode())
    assert "uptime" in str(excinfo.value)


def test_mac_keys_are_canonicalised(info_async_bytes: bytes) -> None:
    data = json.loads(info_async_bytes)
    clients = data[1]["02:00:00:00:00:01"]["2.4 GHz"]["User network"]
    clients["02-00-00-00-00-AB"] = clients.pop("02:00:00:00:00:10")
    snapshot = parse_info_async(json.dumps(data).encode())
    assert any(c.mac == "02:00:00:00:00:ab" for c in snapshot.clients)
