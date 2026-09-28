"""Tests for the AirPlay mDNS probe: polling logic against a fake resolver, the scraper, and the collector."""

import logging
from dataclasses import replace
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.airplay.collector import AirplayCollector
from justdavis_monitoring_exporters.airplay.main import AirplayScraper, AirplaySettings, build_registry
from justdavis_monitoring_exporters.airplay.models import (
    AirplaySnapshot,
    PlaybackState,
    ServiceKey,
    ServiceObservation,
    UnicastAnswer,
)
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
    shared_dir=Path("/nonexistent"),
)
NO_ADDRESSES: dict[str, str] = {}
# The record forms seen on 2026-09-28, with made-up group ids.
IDLE_TXT = {"flags": "0x98404", "gid": "00000000-0000-4000-8000-00000000000A", "igl": "1"}
FOLLOWING_AND_PLAYING_TXT = {"flags": "0x5b8c04", "gid": "00000000-0000-4000-8000-0000000000F0", "igl": "0"}


class FakeResolver:
    """`resolvable` holds full mDNS instance names that resolve by multicast, `unicast_resolvable`
    those that answer a unicast query, and `unicast_unusable` those whose unicast query cannot be made;
    `discovered` maps (type, instance) pairs, as the passive browser would record them, to last-seen
    times; `txt` maps instance names to the TXT fields their unicast answer carries."""

    def __init__(
        self,
        resolvable: set[str],
        discovered: dict[tuple[str, str], float] | None = None,
        unicast_resolvable: set[str] | None = None,
        unicast_unusable: set[str] | None = None,
        txt: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.txt = txt or {}
        self.resolvable = resolvable
        self.unicast_resolvable = unicast_resolvable or set()
        self.unicast_unusable = unicast_unusable or set()
        self._discovered = discovered or {}
        self.requests: list[tuple[str, str]] = []
        self.unicast_requests: list[tuple[str, str, str]] = []
        self.error: Exception | None = None

    def resolve(self, service_type: str, instance_name: str, timeout_ms: int) -> bool:
        self.requests.append((service_type, instance_name))
        if self.error is not None:
            raise self.error
        return instance_name in self.resolvable

    def resolve_unicast(
        self, ip: str, service_type: str, instance_name: str, timeout_ms: int
    ) -> UnicastAnswer | None:
        self.unicast_requests.append((ip, service_type, instance_name))
        if self.error is not None:
            raise self.error
        if instance_name in self.unicast_unusable:
            return None
        if instance_name not in self.unicast_resolvable:
            return UnicastAnswer(answered=False)
        return UnicastAnswer(answered=True, txt=self.txt.get(instance_name))

    def discovered(self) -> dict[tuple[str, str], float]:
        return dict(self._discovered)


def test_airplay_instances_are_resolved_by_name() -> None:
    resolver = FakeResolver({"Speaker A"})
    snapshot = poll(resolver, TRACKED, addresses=NO_ADDRESSES, timeout_ms=10, now=100.0, last_seen={})
    assert (AIRPLAY_TYPE, "Speaker A") in resolver.requests
    assert (AIRPLAY_TYPE, "Speaker B") in resolver.requests
    assert snapshot.services[A_AIRPLAY].resolved is True
    assert snapshot.services[B_AIRPLAY].resolved is False


def test_raop_instances_are_matched_through_passive_discovery_because_they_carry_a_device_id() -> None:
    discovered = {(RAOP_TYPE, "AABBCCDDEEFF@Speaker A"): 50.0}
    resolver = FakeResolver({"AABBCCDDEEFF@Speaker A"}, discovered)
    snapshot = poll(resolver, TRACKED, addresses=NO_ADDRESSES, timeout_ms=10, now=100.0, last_seen={})
    assert (RAOP_TYPE, "AABBCCDDEEFF@Speaker A") in resolver.requests
    assert all(name != "Speaker A" for t, name in resolver.requests if t == RAOP_TYPE)
    assert snapshot.services[A_RAOP].resolved is True
    assert snapshot.services[A_RAOP].discovered is True
    assert snapshot.services[B_RAOP].resolved is False


def test_poll_only_probes_homepods() -> None:
    resolver = FakeResolver(set())
    addresses = {"Tablet": "192.0.2.120", "Speaker-A": "192.0.2.110"}
    poll(resolver, TRACKED, addresses=addresses, timeout_ms=10, now=100.0, last_seen={})
    assert all(name != "Tablet" for _, name in resolver.requests)
    assert [ip for ip, _, _ in resolver.unicast_requests] == ["192.0.2.110"]


def test_unicast_queries_use_the_tracked_name_address_for_the_airplay_service_only() -> None:
    # Addresses are keyed by the tracked name ("Speaker-A", as the AmpliFi exporter labels them), while
    # the instance queried is the AirPlay name ("Speaker A"); RAOP is never queried by unicast.
    resolver = FakeResolver({"Speaker A", "Speaker B"}, unicast_resolvable={"Speaker A"})
    addresses = {"Speaker-A": "192.0.2.110"}
    snapshot = poll(resolver, TRACKED, addresses=addresses, timeout_ms=10, now=100.0, last_seen={})
    assert resolver.unicast_requests == [("192.0.2.110", AIRPLAY_TYPE, "Speaker A")]
    assert snapshot.services[A_AIRPLAY].unicast_resolved is True
    assert snapshot.services[A_RAOP].unicast_resolved is None
    assert snapshot.services[B_AIRPLAY].unicast_resolved is None


def test_multicast_failure_with_a_unicast_answer_is_recorded_and_counts_as_seen() -> None:
    resolver = FakeResolver(set(), unicast_resolvable={"Speaker A"})
    addresses = {"Speaker-A": "192.0.2.110", "Speaker-B": "192.0.2.111"}
    snapshot = poll(resolver, TRACKED, addresses=addresses, timeout_ms=10, now=100.0, last_seen={})
    assert snapshot.services[A_AIRPLAY].resolved is False
    assert snapshot.services[A_AIRPLAY].unicast_resolved is True
    assert snapshot.services[A_AIRPLAY].last_seen == 100.0
    assert snapshot.services[B_AIRPLAY].unicast_resolved is False
    assert snapshot.services[B_AIRPLAY].last_seen is None


def test_unicast_query_that_cannot_be_made_costs_only_that_homepods_unicast_result() -> None:
    resolver = FakeResolver(
        {"Speaker A", "Speaker B"}, unicast_resolvable={"Speaker B"}, unicast_unusable={"Speaker A"}
    )
    addresses = {"Speaker-A": "192.0.2.110", "Speaker-B": "192.0.2.111"}
    snapshot = poll(resolver, TRACKED, addresses=addresses, timeout_ms=10, now=100.0, last_seen={})
    # Not False: the query said nothing about Speaker A, so it must not read as "did not answer".
    assert snapshot.services[A_AIRPLAY].unicast_resolved is None
    assert snapshot.services[A_AIRPLAY].resolved is True
    assert snapshot.services[B_AIRPLAY].unicast_resolved is True
    assert snapshot.services[B_AIRPLAY].resolved is True


def test_playback_state_is_read_from_the_unicast_answer_even_when_multicast_fails() -> None:
    resolver = FakeResolver(
        {"Speaker B"},
        unicast_resolvable={"Speaker A", "Speaker B"},
        txt={"Speaker A": FOLLOWING_AND_PLAYING_TXT, "Speaker B": IDLE_TXT},
    )
    addresses = {"Speaker-A": "192.0.2.110", "Speaker-B": "192.0.2.111"}
    snapshot = poll(resolver, TRACKED, addresses=addresses, timeout_ms=10, now=100.0, last_seen={})
    assert snapshot.services[A_AIRPLAY].resolved is False
    assert snapshot.services[A_AIRPLAY].state == PlaybackState(
        audio_playing=True,
        group_leader=False,
        group_id="00000000-0000-4000-8000-0000000000F0",
        status_flags=0x5B8C04,
    )
    assert snapshot.services[B_AIRPLAY].state == PlaybackState(
        audio_playing=False,
        group_leader=True,
        group_id="00000000-0000-4000-8000-00000000000A",
        status_flags=0x98404,
    )
    assert snapshot.services[A_RAOP].state is None


def test_playback_state_is_unknown_without_an_answer_and_unreadable_from_a_record_in_another_form() -> None:
    resolver = FakeResolver(
        set(), unicast_resolvable={"Speaker B"}, txt={"Speaker A": IDLE_TXT, "Speaker B": {"flags": "0x4"}}
    )
    addresses = {"Speaker-A": "192.0.2.110", "Speaker-B": "192.0.2.111"}
    snapshot = poll(resolver, TRACKED, addresses=addresses, timeout_ms=10, now=100.0, last_seen={})
    # Speaker A did not answer, which says nothing about its record.
    assert snapshot.services[A_AIRPLAY].unicast_resolved is False
    assert snapshot.services[A_AIRPLAY].state is None
    assert snapshot.services[A_AIRPLAY].state_unreadable is None
    # Speaker B answered with a record that lacks the group fields.
    assert snapshot.services[B_AIRPLAY].unicast_resolved is True
    assert snapshot.services[B_AIRPLAY].state is None
    assert snapshot.services[B_AIRPLAY].state_unreadable == "the record has no gid, igl (it has flags)"
    assert snapshot.services[B_AIRPLAY].last_seen == 100.0


def test_answer_without_a_txt_record_is_unreadable() -> None:
    resolver = FakeResolver(set(), unicast_resolvable={"Speaker A"})
    snapshot = poll(
        resolver, TRACKED, addresses={"Speaker-A": "192.0.2.110"}, timeout_ms=10, now=100.0, last_seen={}
    )
    assert snapshot.services[A_AIRPLAY].state_unreadable == "the answer carried no TXT record"


def test_unreadable_record_is_logged_when_it_changes_and_not_every_poll(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "airplay-targets.json").write_bytes(
        b'[{"targets": ["192.0.2.110:7000"], "labels": {"name": "Speaker-A", "kind": "homepod"}}]'
    )
    resolver = FakeResolver(set(), unicast_resolvable={"Speaker A"}, txt={"Speaker A": {"flags": "0x4"}})
    registry, collector, errors = build_registry()
    scraper = AirplayScraper(replace(SETTINGS, shared_dir=tmp_path), resolver, collector, errors)
    a = {"name": "Speaker A"}
    with caplog.at_level(logging.INFO):
        scraper()
        scraper()
        assert [record.levelname for record in caplog.records] == ["WARNING"]
        assert "Speaker A cannot be read" in caplog.text
        assert "no gid, igl (it has flags)" in caplog.text
        assert registry.get_sample_value("airplay_playback_state_readable", a) == 0.0
        assert registry.get_sample_value("airplay_audio_playing", a) is None
        # A poll it does not answer says nothing about the record, so nothing is logged for it.
        resolver.unicast_resolvable = set()
        scraper()
        assert registry.get_sample_value("airplay_playback_state_readable", a) is None
        resolver.unicast_resolvable = {"Speaker A"}
        scraper()
        assert len(caplog.records) == 1
        # The reason changes, then the record becomes readable.
        resolver.txt = {"Speaker A": {"flags": "many", "gid": "g", "igl": "1"}}
        scraper()
        assert [record.levelname for record in caplog.records] == ["WARNING", "WARNING"]
        resolver.txt = {"Speaker A": IDLE_TXT}
        scraper()
        scraper()
        assert [record.levelname for record in caplog.records] == ["WARNING", "WARNING", "INFO"]
        assert "Speaker A can be read again" in caplog.text
        assert registry.get_sample_value("airplay_playback_state_readable", a) == 1.0
        assert registry.get_sample_value("airplay_audio_playing", a) == 0.0


def test_last_seen_comes_from_resolution_then_discovery_then_the_earlier_record() -> None:
    discovered = {(AIRPLAY_TYPE, "Speaker B"): 50.0, (AIRPLAY_TYPE, "Guest Device"): 60.0}
    resolver = FakeResolver({"Speaker A"}, discovered)
    first = poll(resolver, TRACKED, addresses=NO_ADDRESSES, timeout_ms=10, now=100.0, last_seen={})
    assert first.services[A_AIRPLAY].last_seen == 100.0
    assert first.services[B_AIRPLAY].last_seen == 50.0
    assert ServiceKey("Guest Device", "_airplay._tcp") not in first.services
    earlier = {A_AIRPLAY: 100.0, B_AIRPLAY: 50.0}
    second = poll(
        FakeResolver(set()), TRACKED, addresses=NO_ADDRESSES, timeout_ms=10, now=200.0, last_seen=earlier
    )
    assert second.services[A_AIRPLAY].resolved is False
    assert second.services[A_AIRPLAY].last_seen == 100.0
    assert second.services[B_AIRPLAY].last_seen == 50.0


def test_listener_names_feed_poll_without_the_type_suffix() -> None:
    # The browser hands over "<instance>.<type>"; the resolver must be asked for "<instance>" only, or
    # every RAOP query would target "X._raop._tcp.local.._raop._tcp.local." and fail forever.
    listener = _Listener()
    listener.add_service(None, RAOP_TYPE, "AABBCCDDEEFF@Speaker A." + RAOP_TYPE)  # type: ignore[arg-type]
    resolver = FakeResolver({"AABBCCDDEEFF@Speaker A"}, listener.snapshot())
    snapshot = poll(resolver, TRACKED, addresses=NO_ADDRESSES, timeout_ms=10, now=1.0, last_seen={})
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


def test_scraper_reads_addresses_from_the_shared_target_file(tmp_path: Path) -> None:
    (tmp_path / "airplay-targets.json").write_bytes(
        b'[{"targets": ["192.0.2.110:7000"], "labels": {"name": "Speaker-A", "kind": "homepod"}}]'
    )
    resolver = FakeResolver(set(), unicast_resolvable={"Speaker A"})
    registry, collector, errors = build_registry()
    AirplayScraper(replace(SETTINGS, shared_dir=tmp_path), resolver, collector, errors)()
    a = {"name": "Speaker A", "service": "_airplay._tcp"}
    b = {"name": "Speaker B", "service": "_airplay._tcp"}
    assert registry.get_sample_value("airplay_service_resolved", a) == 0.0
    assert registry.get_sample_value("airplay_service_unicast_resolved", a) == 1.0
    assert registry.get_sample_value("airplay_service_unicast_resolved", b) is None


def test_collector_exports_playback_state_only_for_homepods_whose_record_was_read() -> None:
    playing = PlaybackState(
        audio_playing=True,
        group_leader=False,
        group_id="00000000-0000-4000-8000-0000000000F0",
        status_flags=0x5B8C04,
    )
    snapshot = AirplaySnapshot(
        services={
            A_AIRPLAY: ServiceObservation(
                resolved=False, discovered=False, last_seen=1.0, unicast_resolved=True, state=playing
            ),
            B_AIRPLAY: ServiceObservation(
                resolved=True, discovered=False, last_seen=1.0, unicast_resolved=False
            ),
        }
    )
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial().succeeded(timestamp=1.0, duration_seconds=0.1))
    collector = AirplayCollector(statuses)
    collector.publish(snapshot)
    registry = CollectorRegistry()
    registry.register(collector)
    a = {"name": "Speaker A"}
    group = {"name": "Speaker A", "group": "00000000-0000-4000-8000-0000000000F0"}
    assert registry.get_sample_value("airplay_playback_state_readable", a) == 1.0
    assert registry.get_sample_value("airplay_audio_playing", a) == 1.0
    assert registry.get_sample_value("airplay_group_leader", a) == 0.0
    assert registry.get_sample_value("airplay_group_info", group) == 1.0
    assert registry.get_sample_value("airplay_status_flags", a) == float(0x5B8C04)
    # Absent, not 0: Speaker B did not answer, which says nothing about what it is playing.
    playback = {
        sample.name
        for family in collector.collect()
        for sample in family.samples
        if sample.labels.get("name") == "Speaker B" and "service" not in sample.labels
    }
    assert playback == set()


def test_collector_exports_resolved_discovered_unicast_and_last_seen() -> None:
    snapshot = AirplaySnapshot(
        services={
            A_AIRPLAY: ServiceObservation(resolved=True, discovered=False, last_seen=None),
            B_AIRPLAY: ServiceObservation(
                resolved=False, discovered=True, last_seen=50.0, unicast_resolved=True
            ),
            B_RAOP: ServiceObservation(
                resolved=False, discovered=False, last_seen=None, unicast_resolved=False
            ),
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
    assert registry.get_sample_value("airplay_service_unicast_resolved", a) is None
    assert registry.get_sample_value("airplay_service_unicast_resolved", b) == 1.0
    assert (
        registry.get_sample_value("airplay_service_unicast_resolved", {**b, "service": "_raop._tcp"}) == 0.0
    )
    assert registry.get_sample_value("airplay_service_last_seen_timestamp_seconds", b) == 50.0
    assert registry.get_sample_value("airplay_service_last_seen_timestamp_seconds", a) is None
