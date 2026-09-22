"""Tests for the AirPlay mDNS probe: polling logic against a fake resolver, the scraper, and the collector."""

import pytest
from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.airplay.collector import AirplayCollector
from justdavis_monitoring_exporters.airplay.main import AirplayScraper, AirplaySettings, build_registry
from justdavis_monitoring_exporters.airplay.models import AirplaySnapshot, ServiceKey, ServiceObservation
from justdavis_monitoring_exporters.airplay.resolver import AIRPLAY_TYPE, RAOP_TYPE, _Listener, poll
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder
from tests.helpers import tracked

TRACKED = (
    tracked("02:00:00:00:00:10", "Speaker-A", "homepod", "Speaker A"),
    tracked("02:00:00:00:00:11", "Speaker-B", "homepod", "Speaker B"),
    tracked("02:00:00:00:00:20", "Tablet", "ipad"),
)
A_AIRPLAY = ServiceKey("Speaker A", "_airplay._tcp")
A_RAOP = ServiceKey("Speaker A", "_raop._tcp")
B_AIRPLAY = ServiceKey("Speaker B", "_airplay._tcp")
B_RAOP = ServiceKey("Speaker B", "_raop._tcp")
SETTINGS = AirplaySettings(
    lan_ip="192.0.2.10",
    port=9803,
    listen_addr="127.0.0.1",
    interval_seconds=30,
    resolve_timeout_ms=10,
    tracked_clients=TRACKED,
)


class FakeResolver:
    """`resolvable` holds full mDNS instance names that resolve; `discovered` maps (type, instance)
    pairs, as the passive browser would record them, to last-seen times."""

    def __init__(self, resolvable: set[str], discovered: dict[tuple[str, str], float] | None = None) -> None:
        self.resolvable = resolvable
        self._discovered = discovered or {}
        self.requests: list[tuple[str, str]] = []
        self.error: Exception | None = None

    def resolve(self, service_type: str, instance_name: str, timeout_ms: int) -> bool:
        self.requests.append((service_type, instance_name))
        if self.error is not None:
            raise self.error
        return instance_name in self.resolvable

    def discovered(self) -> dict[tuple[str, str], float]:
        return dict(self._discovered)


def test_airplay_instances_are_resolved_by_name() -> None:
    resolver = FakeResolver({"Speaker A"})
    snapshot = poll(resolver, TRACKED, timeout_ms=10, now=100.0, last_seen={})
    assert (AIRPLAY_TYPE, "Speaker A") in resolver.requests
    assert (AIRPLAY_TYPE, "Speaker B") in resolver.requests
    assert snapshot.services[A_AIRPLAY].resolved is True
    assert snapshot.services[B_AIRPLAY].resolved is False


def test_raop_instances_are_matched_through_passive_discovery_because_they_carry_a_device_id() -> None:
    discovered = {(RAOP_TYPE, "AABBCCDDEEFF@Speaker A"): 50.0}
    resolver = FakeResolver({"AABBCCDDEEFF@Speaker A"}, discovered)
    snapshot = poll(resolver, TRACKED, timeout_ms=10, now=100.0, last_seen={})
    assert (RAOP_TYPE, "AABBCCDDEEFF@Speaker A") in resolver.requests
    assert all(name != "Speaker A" for t, name in resolver.requests if t == RAOP_TYPE)
    assert snapshot.services[A_RAOP].resolved is True
    assert snapshot.services[A_RAOP].discovered is True
    assert snapshot.services[B_RAOP].resolved is False


def test_poll_only_probes_homepods() -> None:
    resolver = FakeResolver(set())
    poll(resolver, TRACKED, timeout_ms=10, now=100.0, last_seen={})
    assert all(name != "Tablet" for _, name in resolver.requests)


def test_last_seen_comes_from_resolution_then_discovery_then_the_earlier_record() -> None:
    discovered = {(AIRPLAY_TYPE, "Speaker B"): 50.0, (AIRPLAY_TYPE, "Guest Device"): 60.0}
    resolver = FakeResolver({"Speaker A"}, discovered)
    first = poll(resolver, TRACKED, timeout_ms=10, now=100.0, last_seen={})
    assert first.services[A_AIRPLAY].last_seen == 100.0
    assert first.services[B_AIRPLAY].last_seen == 50.0
    assert ServiceKey("Guest Device", "_airplay._tcp") not in first.services
    earlier = {A_AIRPLAY: 100.0, B_AIRPLAY: 50.0}
    second = poll(FakeResolver(set()), TRACKED, timeout_ms=10, now=200.0, last_seen=earlier)
    assert second.services[A_AIRPLAY].resolved is False
    assert second.services[A_AIRPLAY].last_seen == 100.0
    assert second.services[B_AIRPLAY].last_seen == 50.0


def test_listener_names_feed_poll_without_the_type_suffix() -> None:
    # The browser hands over "<instance>.<type>"; the resolver must be asked for "<instance>" only, or
    # every RAOP query would target "X._raop._tcp.local.._raop._tcp.local." and fail forever.
    listener = _Listener()
    listener.add_service(None, RAOP_TYPE, "AABBCCDDEEFF@Speaker A." + RAOP_TYPE)  # type: ignore[arg-type]
    resolver = FakeResolver({"AABBCCDDEEFF@Speaker A"}, listener.snapshot())
    snapshot = poll(resolver, TRACKED, timeout_ms=10, now=1.0, last_seen={})
    assert (RAOP_TYPE, "AABBCCDDEEFF@Speaker A") in resolver.requests
    assert snapshot.services[A_RAOP].resolved is True
    listener.remove_service(None, RAOP_TYPE, "AABBCCDDEEFF@Speaker A." + RAOP_TYPE)  # type: ignore[arg-type]
    assert listener.snapshot() == {}


def test_service_key_from_mdns_strips_type_and_device_id() -> None:
    assert ServiceKey.from_mdns(AIRPLAY_TYPE, "Speaker A._airplay._tcp.local.") == A_AIRPLAY
    assert ServiceKey.from_mdns(RAOP_TYPE, "AABBCCDDEEFF@Speaker A._raop._tcp.local.") == A_RAOP


def test_resolver_error_fails_the_poll_and_keeps_last_seen_for_the_next_one() -> None:
    resolver = FakeResolver({"Speaker A"})
    registry, collector, errors = build_registry()
    scraper = AirplayScraper(SETTINGS, resolver, collector, errors)
    scraper()
    a = {"name": "Speaker A", "service": "_airplay._tcp"}
    assert registry.get_sample_value("airplay_service_resolved", a) == 1.0
    first_seen = registry.get_sample_value("airplay_service_last_seen_timestamp_seconds", a)
    resolver.error = RuntimeError("zeroconf not running")
    with pytest.raises(RuntimeError):
        scraper()
    assert registry.get_sample_value("airplay_service_resolved", a) is None
    assert registry.get_sample_value("airplay_scrape_errors_total", {"stage": "resolve"}) == 1.0
    resolver.error = None
    resolver.resolvable = set()
    scraper()
    assert registry.get_sample_value("airplay_service_resolved", a) == 0.0
    assert registry.get_sample_value("airplay_service_last_seen_timestamp_seconds", a) == first_seen


def test_collector_exports_resolved_discovered_and_last_seen() -> None:
    snapshot = AirplaySnapshot(
        services={
            A_AIRPLAY: ServiceObservation(resolved=True, discovered=False, last_seen=None),
            B_AIRPLAY: ServiceObservation(resolved=False, discovered=True, last_seen=50.0),
        }
    )
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial().succeeded(timestamp=1.0, duration_seconds=0.1))
    collector = AirplayCollector(statuses)
    collector.publish(snapshot)
    registry = CollectorRegistry()
    registry.register(collector)
    a = {"name": "Speaker A", "service": "_airplay._tcp"}
    b = {"name": "Speaker B", "service": "_airplay._tcp"}
    assert registry.get_sample_value("airplay_up") == 1.0
    assert registry.get_sample_value("airplay_service_resolved", a) == 1.0
    assert registry.get_sample_value("airplay_service_resolved", b) == 0.0
    assert registry.get_sample_value("airplay_service_discovered", a) == 0.0
    assert registry.get_sample_value("airplay_service_discovered", b) == 1.0
    assert registry.get_sample_value("airplay_service_last_seen_timestamp_seconds", b) == 50.0
    assert registry.get_sample_value("airplay_service_last_seen_timestamp_seconds", a) is None
