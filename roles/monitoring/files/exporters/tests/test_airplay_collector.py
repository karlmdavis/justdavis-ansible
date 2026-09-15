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
A_AIRPLAY = ServiceKey("Speaker A", "_airplay._tcp")
A_RAOP = ServiceKey("Speaker A", "_raop._tcp")
B_AIRPLAY = ServiceKey("Speaker B", "_airplay._tcp")
B_RAOP = ServiceKey("Speaker B", "_raop._tcp")


class FakeResolver:
    """`resolvable` holds full mDNS instance names that resolve; `discovered` maps (type, instance)
    pairs, as the passive browser would record them, to last-seen times."""

    def __init__(self, resolvable: set[str], discovered: dict[tuple[str, str], float] | None = None) -> None:
        self.resolvable = resolvable
        self._discovered = discovered or {}
        self.requests: list[tuple[str, str]] = []

    def resolve(self, service_type: str, instance_name: str, timeout_ms: int) -> bool:
        self.requests.append((service_type, instance_name))
        if instance_name == "boom":
            raise RuntimeError("zeroconf not running")
        return instance_name in self.resolvable

    def discovered(self) -> dict[tuple[str, str], float]:
        return dict(self._discovered)


def test_airplay_instances_are_resolved_by_name() -> None:
    resolver = FakeResolver({"Speaker A"})
    snapshot = poll(resolver, TRACKED, timeout_ms=10, now=100.0)
    assert (AIRPLAY_TYPE, "Speaker A") in resolver.requests
    assert (AIRPLAY_TYPE, "Speaker B") in resolver.requests
    assert snapshot.resolved[A_AIRPLAY] is True
    assert snapshot.resolved[B_AIRPLAY] is False


def test_raop_instances_are_matched_through_passive_discovery_because_they_carry_a_device_id() -> None:
    discovered = {(RAOP_TYPE, "AABBCCDDEEFF@Speaker A"): 50.0}
    resolver = FakeResolver({"AABBCCDDEEFF@Speaker A"}, discovered)
    snapshot = poll(resolver, TRACKED, timeout_ms=10, now=100.0)
    assert (RAOP_TYPE, "AABBCCDDEEFF@Speaker A") in resolver.requests
    assert all(name != "Speaker A" for t, name in resolver.requests if t == RAOP_TYPE)
    assert snapshot.resolved[A_RAOP] is True
    assert snapshot.resolved[B_RAOP] is False
    assert A_RAOP in snapshot.discovered


def test_poll_only_probes_homepods() -> None:
    resolver = FakeResolver(set())
    poll(resolver, TRACKED, timeout_ms=10, now=100.0)
    assert all(name != "Tablet" for _, name in resolver.requests)


def test_last_seen_comes_from_resolution_then_discovery_then_the_previous_poll() -> None:
    discovered = {(AIRPLAY_TYPE, "Speaker B"): 50.0, (AIRPLAY_TYPE, "Guest Device"): 60.0}
    resolver = FakeResolver({"Speaker A"}, discovered)
    first = poll(resolver, TRACKED, timeout_ms=10, now=100.0)
    assert first.last_seen[A_AIRPLAY] == 100.0
    assert first.last_seen[B_AIRPLAY] == 50.0
    assert ServiceKey("Guest Device", "_airplay._tcp") not in first.last_seen
    quiet = FakeResolver(set())
    second = poll(quiet, TRACKED, timeout_ms=10, now=200.0, previous=first)
    assert second.resolved[A_AIRPLAY] is False
    assert second.last_seen[A_AIRPLAY] == 100.0
    assert second.last_seen[B_AIRPLAY] == 50.0


def test_a_failing_resolve_counts_as_unresolved_without_failing_the_poll() -> None:
    tracked = (TrackedClient(mac="02:00:00:00:00:99", name="Boom", kind="homepod", airplay_name="boom"),)
    snapshot = poll(FakeResolver(set()), tracked, timeout_ms=10, now=1.0)
    assert snapshot.resolved[ServiceKey("boom", "_airplay._tcp")] is False


def test_service_key_from_mdns_strips_type_and_device_id() -> None:
    assert ServiceKey.from_mdns(AIRPLAY_TYPE, "Speaker A._airplay._tcp.local.") == A_AIRPLAY
    assert ServiceKey.from_mdns(RAOP_TYPE, "AABBCCDDEEFF@Speaker A._raop._tcp.local.") == A_RAOP


def test_collector_exports_resolved_discovered_and_last_seen() -> None:
    snapshot = AirplaySnapshot(
        resolved={A_AIRPLAY: True, B_AIRPLAY: False},
        discovered=frozenset({B_AIRPLAY}),
        last_seen={B_AIRPLAY: 50.0},
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
