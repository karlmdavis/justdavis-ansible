"""Tests for the ping_exporter / blackbox target files rendered from the AmpliFi snapshot."""

import json
from dataclasses import replace
from pathlib import Path

from justdavis_monitoring_exporters.amplifi.parser import parse_info_async
from justdavis_monitoring_exporters.amplifi.targets import (
    PingConfig,
    TargetInfo,
    render_airplay_targets,
    render_ping_targets,
    target_infos,
)
from justdavis_monitoring_exporters.common.settings import TrackedClient

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = parse_info_async((FIXTURES / "amplifi_info_async.json").read_bytes())

TRACKED = (
    TrackedClient(mac="02:00:00:00:00:10", name="Speaker A", kind="homepod", airplay_name="Speaker A"),
    TrackedClient(mac="02:00:00:00:00:11", name="Speaker B", kind="homepod", airplay_name="Speaker B"),
    TrackedClient(mac="02:00:00:00:00:20", name="Tablet", kind="ipad", airplay_name="Tablet"),
    TrackedClient(mac="02:00:00:00:00:99", name="Absent", kind="laptop", airplay_name="Absent"),
)
STATIC = ("1.1.1.1", "8.8.8.8")
PING = PingConfig(interval_seconds=1, timeout_seconds=2, history_size=60)


def test_target_infos_cover_static_targets_mesh_points_and_present_tracked_clients() -> None:
    infos = target_infos(STATIC, SNAPSHOT, TRACKED)
    assert set(infos) == {
        TargetInfo(ip="1.1.1.1", name="1.1.1.1", kind="static"),
        TargetInfo(ip="8.8.8.8", name="8.8.8.8", kind="static"),
        TargetInfo(ip="192.0.2.1", name="test-router", kind="router"),
        TargetInfo(ip="192.0.2.239", name="Kitchen", kind="mesh_point"),
        TargetInfo(ip="192.0.2.234", name="Living Room", kind="mesh_point"),
        TargetInfo(ip="192.0.2.110", name="Speaker A", kind="homepod"),
        TargetInfo(ip="192.0.2.212", name="Speaker B", kind="homepod"),
        TargetInfo(ip="192.0.2.120", name="Tablet", kind="ipad"),
    }


def test_target_infos_are_ordered_deterministically() -> None:
    assert target_infos(STATIC, SNAPSHOT, TRACKED) == target_infos(STATIC, SNAPSHOT, TRACKED)
    ips = [info.ip for info in target_infos(STATIC, SNAPSHOT, TRACKED)]
    assert ips[:2] == ["1.1.1.1", "8.8.8.8"]


def test_ping_targets_yaml_has_ping_block_and_every_target() -> None:
    text = render_ping_targets(PING, target_infos(STATIC, SNAPSHOT, TRACKED)).decode()
    assert text.startswith("ping:\n  interval: 1s\n  timeout: 2s\n  history-size: 60\ntargets:\n")
    for ip in ("1.1.1.1", "8.8.8.8", "192.0.2.1", "192.0.2.239", "192.0.2.110", "192.0.2.120"):
        assert f"  - {ip}\n" in text


def test_ping_targets_with_no_snapshot_still_lists_static_targets() -> None:
    text = render_ping_targets(PING, target_infos(STATIC, None, TRACKED)).decode()
    assert text.endswith("targets:\n  - 1.1.1.1\n  - 8.8.8.8\n")


def test_airplay_targets_lists_only_present_homepods_on_port_7000() -> None:
    data = json.loads(render_airplay_targets(SNAPSHOT, TRACKED))
    assert data == [
        {"targets": ["192.0.2.110:7000"], "labels": {"name": "Speaker A", "kind": "homepod"}},
        {"targets": ["192.0.2.212:7000"], "labels": {"name": "Speaker B", "kind": "homepod"}},
    ]


def test_airplay_targets_without_snapshot_is_empty_list() -> None:
    assert json.loads(render_airplay_targets(None, TRACKED)) == []


def test_the_router_listed_as_a_static_target_is_described_once_as_static() -> None:
    # In production the router's address is both a static ping target and the topology's router; a
    # second description with the same ip would break `on (ip)` joins in the dashboards.
    infos = target_infos(("192.0.2.1", *STATIC), SNAPSHOT, TRACKED)
    matching = [info for info in infos if info.ip == "192.0.2.1"]
    assert matching == [TargetInfo(ip="192.0.2.1", name="192.0.2.1", kind="static")]


def test_a_client_with_an_unusable_address_is_left_out_of_the_targets() -> None:
    broken = tuple(replace(c, ip="") if c.mac == "02:00:00:00:00:10" else c for c in SNAPSHOT.clients)
    snapshot = replace(SNAPSHOT, clients=broken)
    infos = target_infos(STATIC, snapshot, TRACKED)
    assert "Speaker A" not in {info.name for info in infos}
    assert b"\n  - \n" not in render_ping_targets(PING, infos)
    assert "Speaker A" not in render_airplay_targets(snapshot, TRACKED).decode()
