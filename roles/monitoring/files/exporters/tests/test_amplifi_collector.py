"""Tests for the AmpliFi Prometheus collector (snapshot -> metric families)."""

from dataclasses import replace
from pathlib import Path

from prometheus_client import CollectorRegistry, generate_latest

from justdavis_monitoring_exporters.amplifi.collector import AmplifiCollector, CounterKey
from justdavis_monitoring_exporters.amplifi.models import AmplifiSnapshot
from justdavis_monitoring_exporters.amplifi.parser import parse_info_async
from justdavis_monitoring_exporters.amplifi.targets import TargetInfo
from justdavis_monitoring_exporters.common.counters import Unwrapper32
from justdavis_monitoring_exporters.common.settings import TrackedClient
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = parse_info_async((FIXTURES / "amplifi_info_async.json").read_bytes())
TRACKED = (
    TrackedClient(mac="02:00:00:00:00:10", name="Speaker A", kind="homepod", airplay_name="Speaker A"),
    TrackedClient(mac="02:00:00:00:00:11", name="Speaker B", kind="homepod", airplay_name="Speaker B"),
)
SPEAKER_A = {"mac": "02:00:00:00:00:10", "name": "Speaker A", "kind": "homepod"}
TABLET = {"mac": "02:00:00:00:00:20", "name": "", "kind": "other"}


def registry_with(
    snapshot: AmplifiSnapshot | None, status: ScrapeStatus | None = None
) -> tuple[CollectorRegistry, AmplifiCollector]:
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(status or ScrapeStatus.initial())
    collector = AmplifiCollector(statuses, TRACKED, Unwrapper32[CounterKey]())
    collector.set_targets((TargetInfo(ip="1.1.1.1", name="1.1.1.1", kind="static"),))
    if snapshot is not None:
        collector.publish(snapshot)
    registry = CollectorRegistry()
    registry.register(collector)
    return registry, collector


def test_status_metrics_present_without_a_snapshot() -> None:
    registry, _ = registry_with(None)
    assert registry.get_sample_value("amplifi_up") == 0.0
    assert registry.get_sample_value("amplifi_last_success_timestamp_seconds") == 0.0
    assert registry.get_sample_value("amplifi_client_signal_quality", SPEAKER_A) is None


def test_status_metrics_reflect_a_successful_scrape() -> None:
    status = ScrapeStatus.initial().succeeded(timestamp=1700000000.0, duration_seconds=0.25)
    registry, _ = registry_with(SNAPSHOT, status)
    assert registry.get_sample_value("amplifi_up") == 1.0
    assert registry.get_sample_value("amplifi_last_success_timestamp_seconds") == 1700000000.0
    assert registry.get_sample_value("amplifi_scrape_duration_seconds") == 0.25


def test_tracked_client_gauges_carry_configured_name_and_kind() -> None:
    registry, _ = registry_with(SNAPSHOT)
    assert registry.get_sample_value("amplifi_client_signal_quality", SPEAKER_A) == 74.0
    assert registry.get_sample_value("amplifi_client_happiness_score", SPEAKER_A) == 75.0
    assert registry.get_sample_value("amplifi_client_rx_bits_per_second", SPEAKER_A) == 52_000_000.0
    assert registry.get_sample_value("amplifi_client_tx_bits_per_second", SPEAKER_A) == 11_000_000.0
    assert registry.get_sample_value("amplifi_client_inactive_seconds", SPEAKER_A) == 30.0


def test_untracked_client_is_exported_as_other_with_blank_name() -> None:
    registry, _ = registry_with(SNAPSHOT)
    assert registry.get_sample_value("amplifi_client_signal_quality", TABLET) == 89.0


def test_client_info_describes_association() -> None:
    registry, _ = registry_with(SNAPSHOT)
    labels = {
        **SPEAKER_A,
        "ap": "02:00:00:00:00:01",
        "ap_name": "test-router",
        "band": "2.4 GHz",
        "network": "User network",
        "mode": "802.11n",
    }
    assert registry.get_sample_value("amplifi_client_info", labels) == 1.0


def test_client_on_mesh_point_has_that_ap_name() -> None:
    registry, _ = registry_with(SNAPSHOT)
    labels = {
        "mac": "02:00:00:00:00:11",
        "name": "Speaker B",
        "kind": "homepod",
        "ap": "02:00:00:00:00:02",
        "ap_name": "Kitchen",
        "band": "5 GHz",
        "network": "User network",
        "mode": "802.11n",
    }
    assert registry.get_sample_value("amplifi_client_info", labels) == 1.0


def test_backhaul_links_are_labelled_as_backhaul() -> None:
    registry, _ = registry_with(SNAPSHOT)
    labels = {"mac": "02:00:00:00:00:f2", "name": "", "kind": "backhaul"}
    assert registry.get_sample_value("amplifi_client_signal_quality", labels) == 97.0


def test_byte_counters_are_unwrapped_across_snapshots() -> None:
    registry, collector = registry_with(SNAPSHOT)
    first = registry.get_sample_value("amplifi_client_rx_bytes_total", SPEAKER_A)
    assert first == 4294967200.0
    wrapped = _with_rx_bytes(SNAPSHOT, "02:00:00:00:00:10", 50)
    collector.publish(wrapped)
    assert registry.get_sample_value("amplifi_client_rx_bytes_total", SPEAKER_A) == 4294967200.0 + 96 + 50


def test_clearing_the_snapshot_restarts_counter_baselines() -> None:
    registry, collector = registry_with(SNAPSHOT)
    collector.clear()
    assert registry.get_sample_value("amplifi_client_rx_bytes_total", SPEAKER_A) is None
    collector.publish(_with_rx_bytes(SNAPSHOT, "02:00:00:00:00:10", 50))
    assert registry.get_sample_value("amplifi_client_rx_bytes_total", SPEAKER_A) == 50.0


def test_a_client_that_roams_to_another_access_point_restarts_its_baseline() -> None:
    registry, collector = registry_with(SNAPSHOT)
    assert registry.get_sample_value("amplifi_client_rx_bytes_total", SPEAKER_A) == 4294967200.0
    speaker = next(c for c in SNAPSHOT.clients if c.mac == "02:00:00:00:00:10")
    roamed = replace(speaker, ap_mac="02:00:00:00:00:02", rx_bytes=50)
    others = tuple(c for c in SNAPSHOT.clients if c.mac != "02:00:00:00:00:10")
    collector.publish(replace(SNAPSHOT, clients=(*others, roamed)))
    # The new association's counter starts near zero: a reset, not a 4 GiB wrap.
    assert registry.get_sample_value("amplifi_client_rx_bytes_total", SPEAKER_A) == 50.0


def test_a_client_listed_under_two_access_points_is_emitted_once() -> None:
    stale = replace(
        next(c for c in SNAPSHOT.clients if c.mac == "02:00:00:00:00:10"),
        ap_mac="02:00:00:00:00:02",
        inactive_seconds=900,
        signal_quality=10,
    )
    snapshot = replace(SNAPSHOT, clients=(*SNAPSHOT.clients, stale))
    registry, _ = registry_with(snapshot)
    assert registry.get_sample_value("amplifi_client_signal_quality", SPEAKER_A) == 74.0
    exposition = generate_latest(registry).decode()
    assert exposition.count('amplifi_client_signal_quality{kind="homepod",mac="02:00:00:00:00:10"') == 1


def test_mesh_point_and_router_metrics() -> None:
    registry, _ = registry_with(SNAPSHOT)
    kitchen = {"mac": "02:00:00:00:00:02", "name": "Kitchen"}
    assert registry.get_sample_value("amplifi_mesh_point_rssi_min_dbm", kitchen) == -44.0
    assert registry.get_sample_value("amplifi_mesh_point_uptime_seconds", kitchen) == 2008337.0
    assert (
        registry.get_sample_value(
            "amplifi_mesh_point_info", {**kitchen, "backhaul_band": "2.4 GHz", "platform": "AFi-P-HD"}
        )
        == 1.0
    )
    assert registry.get_sample_value("amplifi_router_uptime_seconds") == 33652.0


def test_wan_port_metrics() -> None:
    registry, _ = registry_with(SNAPSHOT)
    assert registry.get_sample_value("amplifi_wan_link") == 1.0
    assert registry.get_sample_value("amplifi_wan_rx_bits_per_second") == 21_931_000.0
    assert registry.get_sample_value("amplifi_wan_tx_bits_per_second") == 542_000.0


def test_airplay_advertised_from_router_view_for_tracked_homepods() -> None:
    registry, _ = registry_with(SNAPSHOT)
    assert (
        registry.get_sample_value(
            "amplifi_client_airplay_advertised", {**SPEAKER_A, "service": "_airplay._tcp"}
        )
        == 1.0
    )
    assert (
        registry.get_sample_value(
            "amplifi_client_airplay_advertised",
            {"mac": "02:00:00:00:00:11", "name": "Speaker B", "kind": "homepod", "service": "_airplay._tcp"},
        )
        == 0.0
    )


def test_target_info_is_exported_from_the_targets_holder() -> None:
    registry, _ = registry_with(SNAPSHOT)
    assert (
        registry.get_sample_value(
            "monitoring_target_info", {"ip": "1.1.1.1", "name": "1.1.1.1", "kind": "static"}
        )
        == 1.0
    )


def test_names_and_bands_from_the_router_are_sanitised_in_every_family() -> None:
    # A mesh point name with curly quotes and a control character, and a band with a stray byte.
    kitchen = next(mp for mp in SNAPSHOT.mesh_points if mp.name == "Kitchen")
    odd = replace(kitchen, name="Karl\u2019s Kitchen\n\x07", backhaul_band="2.4 GHz\x00")
    snapshot = replace(
        SNAPSHOT, mesh_points=tuple(odd if mp is kitchen else mp for mp in SNAPSHOT.mesh_points)
    )
    registry, _ = registry_with(snapshot)
    labels = {"mac": kitchen.mac, "name": "Karl's Kitchen"}
    assert registry.get_sample_value("amplifi_mesh_point_uptime_seconds", labels) is not None
    info = {**labels, "backhaul_band": "2.4 GHz", "platform": "AFi-P-HD"}
    assert registry.get_sample_value("amplifi_mesh_point_info", info) == 1.0
    assert "\x07" not in generate_latest(registry).decode()


def test_tracked_clients_report_whether_they_are_on_the_wifi() -> None:
    registry, collector = registry_with(SNAPSHOT)
    assert registry.get_sample_value("amplifi_tracked_client_associated", SPEAKER_A) == 1.0
    gone = tuple(c for c in SNAPSHOT.clients if c.mac != "02:00:00:00:00:10")
    collector.publish(replace(SNAPSHOT, clients=gone))
    assert registry.get_sample_value("amplifi_tracked_client_associated", SPEAKER_A) == 0.0
    assert registry.get_sample_value("amplifi_client_signal_quality", SPEAKER_A) is None


def _with_rx_bytes(snapshot: AmplifiSnapshot, mac: str, rx_bytes: int) -> AmplifiSnapshot:
    clients = tuple(replace(c, rx_bytes=rx_bytes) if c.mac == mac else c for c in snapshot.clients)
    return replace(snapshot, clients=clients)
