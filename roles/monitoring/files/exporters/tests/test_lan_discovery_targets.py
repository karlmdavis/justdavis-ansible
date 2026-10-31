"""Tests for the probe target files rendered from the device address inventory."""

import json
from pathlib import Path

from justdavis_monitoring_exporters.common.device_addresses import AddressInventory, Sighting, SourceStore
from justdavis_monitoring_exporters.common.settings import Mac
from justdavis_monitoring_exporters.lan_discovery.targets import (
    PingConfig,
    TargetInfo,
    render_airplay_targets,
    render_ping_targets,
    target_infos,
)
from tests.helpers import tracked

TRACKED = (
    tracked("02:00:00:00:00:10", "Speaker A", "homepod", airplay_name="Speaker A's Room"),
    tracked("02:00:00:00:00:11", "Speaker B", "homepod"),
    tracked("02:00:00:00:00:20", "Tablet", "ipad"),
    tracked("02:00:00:00:00:99", "Absent", "laptop"),
)
STATIC = ("1.1.1.1", "8.8.8.8")
PING = PingConfig(interval_seconds=1, timeout_seconds=2, history_size=60)
NOW = 1_000_000.0
MAX_AGE = 180.0


def inventory(tmp_path: Path, *, speaker_b_age: float = 10.0) -> AddressInventory:
    router = SourceStore(tmp_path, "router")
    router.record(
        [
            Sighting(
                address="192.0.2.1",
                observed_at=NOW - 10,
                mac=Mac("02:00:00:00:00:01"),
                name="test-router",
                role="router",
            ),
            Sighting(
                address="192.0.2.239",
                observed_at=NOW - 10,
                mac=Mac("02:00:00:00:00:02"),
                name="Kitchen",
                role="mesh_point",
            ),
            Sighting(
                address="192.0.2.110",
                observed_at=NOW - 20,
                mac=Mac("02:00:00:00:00:10"),
                hostname="Speaker-A",
            ),
            Sighting(address="192.0.2.212", observed_at=NOW - speaker_b_age, mac=Mac("02:00:00:00:00:11")),
            Sighting(address="192.0.2.120", observed_at=NOW - 10, mac=Mac("02:00:00:00:00:20")),
        ],
        now=NOW,
    )
    SourceStore(tmp_path, "arp").record(
        [Sighting(address="192.0.2.110", observed_at=NOW - 5, mac=Mac("02:00:00:00:00:10"))], now=NOW
    )
    return AddressInventory.load(tmp_path)


def test_target_infos_cover_static_targets_infrastructure_and_fresh_tracked_clients(tmp_path: Path) -> None:
    infos = target_infos(STATIC, inventory(tmp_path), TRACKED, now=NOW, max_age=MAX_AGE)
    assert set(infos) == {
        TargetInfo(ip="1.1.1.1", name="1.1.1.1", kind="static"),
        TargetInfo(ip="8.8.8.8", name="8.8.8.8", kind="static"),
        TargetInfo(ip="192.0.2.1", name="test-router", kind="router", source="router"),
        TargetInfo(ip="192.0.2.239", name="Kitchen", kind="mesh_point", source="router"),
        TargetInfo(
            ip="192.0.2.110", name="Speaker A", kind="homepod", airplay_name="Speaker A's Room", source="arp"
        ),
        TargetInfo(
            ip="192.0.2.212", name="Speaker B", kind="homepod", airplay_name="Speaker B", source="router"
        ),
        TargetInfo(ip="192.0.2.120", name="Tablet", kind="ipad", airplay_name="Tablet", source="router"),
    }


def test_a_tracked_client_without_a_fresh_address_gets_no_target(tmp_path: Path) -> None:
    infos = target_infos(
        STATIC, inventory(tmp_path, speaker_b_age=MAX_AGE + 1), TRACKED, now=NOW, max_age=MAX_AGE
    )
    assert "Speaker B" not in {info.name for info in infos}
    assert b"192.0.2.212" not in render_ping_targets(PING, infos)
    assert "Speaker B" not in render_airplay_targets(infos).decode()


def test_target_infos_are_ordered_deterministically(tmp_path: Path) -> None:
    inv = inventory(tmp_path)
    first = target_infos(STATIC, inv, TRACKED, now=NOW, max_age=MAX_AGE)
    assert first == target_infos(STATIC, inv, TRACKED, now=NOW, max_age=MAX_AGE)
    assert [info.ip for info in first][:2] == ["1.1.1.1", "8.8.8.8"]


def test_ping_targets_yaml_has_ping_block_and_every_target(tmp_path: Path) -> None:
    text = render_ping_targets(
        PING, target_infos(STATIC, inventory(tmp_path), TRACKED, now=NOW, max_age=MAX_AGE)
    ).decode()
    assert text.startswith("ping:\n  interval: 1s\n  timeout: 2s\n  history-size: 60\ntargets:\n")
    for ip in ("1.1.1.1", "8.8.8.8", "192.0.2.1", "192.0.2.239", "192.0.2.110", "192.0.2.120"):
        assert f"  - {ip}\n" in text


def test_an_empty_inventory_still_lists_the_static_targets(tmp_path: Path) -> None:
    infos = target_infos(STATIC, AddressInventory.load(tmp_path / "empty"), TRACKED, now=NOW, max_age=MAX_AGE)
    assert render_ping_targets(PING, infos).decode().endswith("targets:\n  - 1.1.1.1\n  - 8.8.8.8\n")
    assert json.loads(render_airplay_targets(infos)) == []


def test_airplay_targets_lists_only_homepods_on_port_7000_in_the_old_file_shape(tmp_path: Path) -> None:
    data = json.loads(
        render_airplay_targets(target_infos(STATIC, inventory(tmp_path), TRACKED, now=NOW, max_age=MAX_AGE))
    )
    assert data == [
        {
            "targets": ["192.0.2.110:7000"],
            "labels": {"name": "Speaker A", "kind": "homepod", "airplay_name": "Speaker A's Room"},
        },
        {
            "targets": ["192.0.2.212:7000"],
            "labels": {"name": "Speaker B", "kind": "homepod", "airplay_name": "Speaker B"},
        },
    ]


def test_the_router_listed_as_a_static_target_is_described_once_as_static(tmp_path: Path) -> None:
    # In production the router's address is both a static ping target and the topology's router; a
    # second description with the same ip would break `on (ip)` joins in the dashboards.
    infos = target_infos(("192.0.2.1", *STATIC), inventory(tmp_path), TRACKED, now=NOW, max_age=MAX_AGE)
    assert [info for info in infos if info.ip == "192.0.2.1"] == [
        TargetInfo(ip="192.0.2.1", name="192.0.2.1", kind="static")
    ]


def test_airplay_target_labels_are_sanitised_like_every_other_label(tmp_path: Path) -> None:
    # Otherwise `on (name)` between the port probe and device_address_fresh would never match a name
    # with a curly apostrophe (one HomePod had one until 2026-09-23).
    curly = (tracked("02:00:00:00:00:10", "Parents\u2019 Room", "homepod"),)
    infos = target_infos((), inventory(tmp_path), curly, now=NOW, max_age=MAX_AGE)
    (entry,) = json.loads(render_airplay_targets(infos))
    assert entry["labels"] == {"name": "Parents' Room", "kind": "homepod", "airplay_name": "Parents' Room"}
