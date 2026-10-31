"""Tests for the LAN discovery poll: sources recorded, inventory rendered, failures attributed."""

import ipaddress
import json
import logging
from collections.abc import Iterable
from pathlib import Path

import pytest

from justdavis_monitoring_exporters.common.device_addresses import (
    DEVICE_ADDRESSES_DIR,
    AddressInventory,
    Sighting,
    SourceStore,
)
from justdavis_monitoring_exporters.common.settings import Mac
from justdavis_monitoring_exporters.lan_discovery import main as main_module
from justdavis_monitoring_exporters.lan_discovery.main import (
    LanDiscoveryScraper,
    LanDiscoverySettings,
    NeighbourSource,
    build_registry,
)
from justdavis_monitoring_exporters.lan_discovery.mdns import CachedInstance
from justdavis_monitoring_exporters.lan_discovery.neigh import NUD_REACHABLE, Neighbour
from justdavis_monitoring_exporters.lan_discovery.targets import PingConfig
from tests.helpers import tracked

A = "02:00:00:00:00:10"
B = "02:00:00:00:00:11"
TRACKED = (
    tracked(A, "Speaker-A", "homepod", airplay_name="Speaker A"),
    tracked(B, "Speaker-B", "homepod", airplay_name="Speaker B"),
)
LAN = 5
SUBNET = ipaddress.IPv4Network("192.0.2.0/24")
AIRPLAY = "_airplay._tcp.local."


def settings(tmp_path: Path, *, lan_ip: str | None = "192.0.2.2") -> LanDiscoverySettings:
    return LanDiscoverySettings(
        lan_ip=lan_ip,
        lan_subnet=SUBNET,
        port=9804,
        listen_addr="127.0.0.1",
        interval_seconds=30,
        max_age_seconds=180,
        tracked_clients=TRACKED,
        static_ping_targets=("1.1.1.1",),
        shared_dir=tmp_path,
        ping=PingConfig(interval_seconds=1, timeout_seconds=2, history_size=60),
    )


class FakeBrowser:
    def __init__(self, instances: tuple[CachedInstance, ...] = ()) -> None:
        self.instances = instances
        self.requested: list[str] = []
        self.error: Exception | None = None

    def cached_instances(self, *, now: float) -> tuple[CachedInstance, ...]:
        if self.error is not None:
            raise self.error
        return self.instances

    def hap_instances(self, *, now: float) -> tuple[CachedInstance, ...]:
        return ()

    def request(self, fqdn: str, timeout_ms: int) -> bool:
        self.requested.append(fqdn)
        return True


def speaker_a_instance(address: str, seen_at: float) -> CachedInstance:
    return CachedInstance(
        "_airplay._tcp", "Speaker A", f"Speaker A.{AIRPLAY}", "Speaker-A.local.", {}, ((address, seen_at),)
    )


def test_poll_records_both_sources_renders_the_files_and_publishes_freshness(tmp_path: Path) -> None:
    import time

    now = time.time()
    # The router knows A's hostname and B's address; the neighbour table confirms A's address.
    SourceStore(tmp_path / DEVICE_ADDRESSES_DIR, "router").record(
        [
            Sighting(address="192.0.2.110", observed_at=now - 3000, mac=Mac(A), hostname="Speaker-A"),
            Sighting(address="192.0.2.212", observed_at=now - 10, mac=Mac(B)),
        ],
        now=now,
    )
    browser = FakeBrowser((speaker_a_instance("192.0.2.111", now - 20),))
    neighbours = (Neighbour(LAN, "192.0.2.111", Mac(A), NUD_REACHABLE, 2.0, 2.0),)
    registry, collector, errors, _homekit = build_registry()
    scraper = LanDiscoveryScraper(
        settings(tmp_path),
        collector,
        errors,
        mdns=browser,
        neighbours=NeighbourSource(LAN, SUBNET),
        read_neighbour_table=lambda: neighbours,
    )
    scraper()
    inventory = AddressInventory.load(tmp_path / DEVICE_ADDRESSES_DIR)
    assert inventory.written_at["mdns"] is not None and inventory.written_at["arp"] is not None
    ping = (tmp_path / "ping-targets.yml").read_text()
    assert ping.endswith("targets:\n  - 1.1.1.1\n  - 192.0.2.111\n  - 192.0.2.212\n")
    airplay = json.loads((tmp_path / "airplay-targets.json").read_text())
    assert [entry["targets"] for entry in airplay] == [["192.0.2.111:7000"], ["192.0.2.212:7000"]]
    a = {"mac": A, "name": "Speaker-A", "kind": "homepod", "airplay_name": "Speaker A", "expect_online": "1"}
    assert registry.get_sample_value("device_address_fresh", a) == 1.0
    assert registry.get_sample_value("device_address_age_seconds", {**a, "source": "arp"}) == pytest.approx(
        2.0, abs=1.0
    )
    assert (
        registry.get_sample_value(
            "monitoring_target_info",
            {"ip": "192.0.2.111", "name": "Speaker-A", "kind": "homepod", "airplay_name": "Speaker A"},
        )
        == 1.0
    )
    assert registry.get_sample_value("lan_discovery_target_files_ok") == 1.0
    assert registry.get_sample_value(
        "device_address_source_last_write_timestamp_seconds", {"source": "router"}
    ) == pytest.approx(now, abs=5)
    assert registry.get_sample_value("device_address_source_enabled", {"source": "mdns"}) == 1.0
    assert registry.get_sample_value("device_address_source_enabled", {"source": "arp"}) == 1.0
    assert browser.requested == []


def test_a_device_with_no_fresh_address_has_no_target_and_is_asked_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    now = time.time()
    SourceStore(tmp_path / DEVICE_ADDRESSES_DIR, "router").record(
        [Sighting(address="192.0.2.110", observed_at=now - 1000, mac=Mac(A), hostname="Speaker-A")],
        now=now - 1000,
    )
    browser = FakeBrowser((speaker_a_instance("192.0.2.110", now - 900),))
    sent: list[str] = []

    def fake_provoke(lan_ip: str, addresses: Iterable[str], *, send: object = None) -> int:
        sent.extend(addresses)
        return len(sent)

    monkeypatch.setattr(main_module, "provoke", fake_provoke)
    registry, collector, errors, _homekit = build_registry()
    scraper = LanDiscoveryScraper(
        settings(tmp_path),
        collector,
        errors,
        mdns=browser,
        neighbours=NeighbourSource(LAN, SUBNET),
        read_neighbour_table=lambda: (),
    )
    scraper()
    a = {"mac": A, "name": "Speaker-A", "kind": "homepod", "airplay_name": "Speaker A", "expect_online": "1"}
    b = {"mac": B, "name": "Speaker-B", "kind": "homepod", "airplay_name": "Speaker B", "expect_online": "1"}
    assert registry.get_sample_value("device_address_fresh", a) == 0.0
    assert registry.get_sample_value("device_address_age_seconds", {**a, "source": "mdns"}) == pytest.approx(
        900, abs=5
    )
    assert registry.get_sample_value("device_address_fresh", b) == 0.0
    assert registry.get_sample_value("device_address_age_seconds", {**b, "source": ""}) is None
    assert "192.0.2.110" not in (tmp_path / "ping-targets.yml").read_text()
    assert json.loads((tmp_path / "airplay-targets.json").read_text()) == []
    assert browser.requested == [f"Speaker A.{AIRPLAY}"]
    assert sent == ["192.0.2.110"]


def test_without_a_lan_address_only_the_static_targets_are_rendered_and_up_is_1(tmp_path: Path) -> None:
    registry, collector, errors, _homekit = build_registry()
    LanDiscoveryScraper(settings(tmp_path, lan_ip=None), collector, errors, mdns=None, neighbours=None)()
    assert (tmp_path / "ping-targets.yml").read_text().endswith("targets:\n  - 1.1.1.1\n")
    assert (tmp_path / "airplay-targets.json").read_text() == "[]\n"
    assert registry.get_sample_value("lan_discovery_target_files_ok") == 1.0
    assert registry.get_sample_value("device_address_source_enabled", {"source": "mdns"}) == 0.0
    assert registry.get_sample_value("device_address_source_enabled", {"source": "arp"}) == 0.0
    assert not (tmp_path / DEVICE_ADDRESSES_DIR).exists()


def test_a_failing_source_is_counted_and_the_render_still_happens(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    browser = FakeBrowser()
    browser.error = RuntimeError("cache unreadable")

    def broken() -> tuple[Neighbour, ...]:
        raise OSError("netlink refused")

    registry, collector, errors, _homekit = build_registry()
    scraper = LanDiscoveryScraper(
        settings(tmp_path),
        collector,
        errors,
        mdns=browser,
        neighbours=NeighbourSource(LAN, SUBNET),
        read_neighbour_table=broken,
    )
    with caplog.at_level(logging.WARNING):
        scraper()
    assert registry.get_sample_value("lan_discovery_scrape_errors_total", {"stage": "resolve"}) == 1.0
    assert registry.get_sample_value("lan_discovery_scrape_errors_total", {"stage": "read"}) == 1.0
    assert (tmp_path / "ping-targets.yml").exists()
    assert registry.get_sample_value("lan_discovery_target_files_ok") == 1.0


def test_an_unwritable_shared_directory_fails_the_poll(tmp_path: Path) -> None:
    registry, collector, errors, _homekit = build_registry()
    scraper = LanDiscoveryScraper(
        settings(tmp_path / "missing", lan_ip=None), collector, errors, mdns=None, neighbours=None
    )
    with pytest.raises(OSError):
        scraper()
    assert registry.get_sample_value("lan_discovery_target_files_ok") == 0.0
    assert registry.get_sample_value("lan_discovery_scrape_errors_total", {"stage": "write"}) == 1.0


def test_a_source_file_that_cannot_be_written_is_a_write_failure_of_the_exporter(tmp_path: Path) -> None:
    (tmp_path / DEVICE_ADDRESSES_DIR).mkdir()
    (tmp_path / DEVICE_ADDRESSES_DIR / "arp.json").mkdir()  # a directory where the file should be
    registry, collector, errors, _homekit = build_registry()
    scraper = LanDiscoveryScraper(
        settings(tmp_path),
        collector,
        errors,
        mdns=None,
        neighbours=NeighbourSource(LAN, SUBNET),
        read_neighbour_table=lambda: (Neighbour(LAN, "192.0.2.110", Mac(A), NUD_REACHABLE, 1.0, 1.0),),
    )
    scraper()
    assert registry.get_sample_value("lan_discovery_scrape_errors_total", {"stage": "write"}) == 1.0
    assert registry.get_sample_value("lan_discovery_scrape_errors_total", {"stage": "read"}) == 0.0
    assert registry.get_sample_value("lan_discovery_target_files_ok") == 1.0
