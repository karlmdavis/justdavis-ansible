"""Tests for the AirPlay mDNS probe: polling logic against a fake resolver, and the collector."""

from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.airplay.collector import AirplayCollector
from justdavis_monitoring_exporters.airplay.models import AirplaySnapshot, ServiceKey
from justdavis_monitoring_exporters.airplay.resolver import AIRPLAY_TYPE, RAOP_TYPE, poll
from justdavis_monitoring_exporters.common.settings import TrackedClient
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder

TRACKED = (
    TrackedClient(mac="02:00:00:00:00:10", name="Speaker-A", kind="homepod", airplay_name="Speaker A"),
    TrackedClient(mac="02:00:00:00:00:11", name="Speaker-B", kind="homepod", airplay_name="Speaker B"),
    TrackedClient(mac="02:00:00:00:00:20", name="Tablet", kind="ipad", airplay_name="Tablet"),
)


class FakeResolver:
    def __init__(self, resolvable: set[str], discovered: dict[ServiceKey, float]) -> None:
        self.resolvable = resolvable
        self._discovered = discovered
        self.requests: list[tuple[str, str]] = []

    def resolve(self, service_type: str, instance_name: str, timeout_ms: int) -> bool:
        self.requests.append((service_type, instance_name))
        return instance_name in self.resolvable

    def discovered(self) -> dict[ServiceKey, float]:
        return dict(self._discovered)


def test_poll_resolves_each_homepod_on_both_airplay_service_types() -> None:
    resolver = FakeResolver({"Speaker A"}, {})
    snapshot = poll(resolver, TRACKED, timeout_ms=10, now=100.0)
    assert set(resolver.requests) == {
        (AIRPLAY_TYPE, "Speaker A"),
        (RAOP_TYPE, "Speaker A"),
        (AIRPLAY_TYPE, "Speaker B"),
        (RAOP_TYPE, "Speaker B"),
    }
    assert snapshot.resolved[ServiceKey("Speaker A", "_airplay._tcp")] is True
    assert snapshot.resolved[ServiceKey("Speaker B", "_airplay._tcp")] is False
    assert snapshot.resolved[ServiceKey("Speaker B", "_raop._tcp")] is False


def test_poll_only_probes_homepods() -> None:
    resolver = FakeResolver(set(), {})
    poll(resolver, TRACKED, timeout_ms=10, now=100.0)
    assert all(name != "Tablet" for _, name in resolver.requests)


def test_poll_records_last_seen_from_resolution_and_passive_discovery() -> None:
    discovered = {
        ServiceKey("Speaker B", "_airplay._tcp"): 50.0,
        ServiceKey("Guest Device", "_airplay._tcp"): 60.0,
    }
    resolver = FakeResolver({"Speaker A"}, discovered)
    snapshot = poll(resolver, TRACKED, timeout_ms=10, now=100.0)
    assert snapshot.last_seen[ServiceKey("Speaker A", "_airplay._tcp")] == 100.0
    assert snapshot.last_seen[ServiceKey("Speaker B", "_airplay._tcp")] == 50.0
    assert ServiceKey("Guest Device", "_airplay._tcp") not in snapshot.last_seen


def test_collector_exports_resolved_discovered_and_last_seen() -> None:
    snapshot = AirplaySnapshot(
        resolved={
            ServiceKey("Speaker A", "_airplay._tcp"): True,
            ServiceKey("Speaker B", "_airplay._tcp"): False,
        },
        discovered={ServiceKey("Speaker B", "_airplay._tcp")},
        last_seen={ServiceKey("Speaker B", "_airplay._tcp"): 50.0},
    )
    snapshots: SnapshotHolder[AirplaySnapshot] = SnapshotHolder()
    snapshots.set(snapshot)
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial().succeeded(timestamp=1.0, duration_seconds=0.1))
    registry = CollectorRegistry()
    registry.register(AirplayCollector(snapshots, statuses))
    a = {"name": "Speaker A", "service": "_airplay._tcp"}
    b = {"name": "Speaker B", "service": "_airplay._tcp"}
    assert registry.get_sample_value("airplay_up") == 1.0
    assert registry.get_sample_value("airplay_service_resolved", a) == 1.0
    assert registry.get_sample_value("airplay_service_resolved", b) == 0.0
    assert registry.get_sample_value("airplay_service_discovered", a) == 0.0
    assert registry.get_sample_value("airplay_service_discovered", b) == 1.0
    assert registry.get_sample_value("airplay_service_last_seen_timestamp_seconds", b) == 50.0
    assert registry.get_sample_value("airplay_service_last_seen_timestamp_seconds", a) is None
