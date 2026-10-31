"""Tests for the device address inventory: per-source stores and the time-of-use collapse."""

import json
import logging
from pathlib import Path

import pytest

from justdavis_monitoring_exporters.common.device_addresses import (
    MAX_ADDRESSES_PER_MAC,
    RETENTION_SECONDS,
    AddressInventory,
    Sighting,
    SourceStore,
    source_file,
)
from justdavis_monitoring_exporters.common.problem_log import ProblemLog
from justdavis_monitoring_exporters.common.settings import Mac

A = Mac("02:00:00:00:00:10")
B = Mac("02:00:00:00:00:11")
ROUTER = Mac("02:00:00:00:00:01")
NOW = 1_000_000.0


def sighting(mac: Mac, address: str, at: float, **kwargs: object) -> Sighting:
    return Sighting(mac=mac, address=address, observed_at=at, **kwargs)  # type: ignore[arg-type]


def test_store_keeps_first_seen_and_moves_last_seen_for_a_repeat_sighting(tmp_path: Path) -> None:
    store = SourceStore(tmp_path, "arp")
    store.record([sighting(A, "192.0.2.110", NOW - 100)], now=NOW - 100)
    store.record([sighting(A, "192.0.2.110", NOW, name="Speaker-A", extra={"state": "REACHABLE"})], now=NOW)
    document = json.loads(source_file(tmp_path, "arp").read_text())
    assert document["source"] == "arp"
    assert document["written_at"] == NOW
    assert document["observations"] == [
        {
            "mac": A,
            "address": "192.0.2.110",
            "first_seen": NOW - 100,
            "last_seen": NOW,
            "name": "Speaker-A",
            "role": "client",
            "extra": {"state": "REACHABLE"},
        }
    ]


def test_store_keeps_old_addresses_as_history_newest_first(tmp_path: Path) -> None:
    store = SourceStore(tmp_path, "router")
    store.record([sighting(A, "192.0.2.110", NOW - 500)], now=NOW - 500)
    store.record([sighting(A, "192.0.2.111", NOW)], now=NOW)
    inventory = AddressInventory.load(tmp_path)
    assert [o.address for o in inventory.observations] == ["192.0.2.111", "192.0.2.110"]
    assert inventory.latest(A, now=NOW, max_age=180) is not None
    assert inventory.latest(A, now=NOW, max_age=180).address == "192.0.2.111"  # type: ignore[union-attr]


def test_store_caps_the_history_per_mac_and_prunes_old_observations(tmp_path: Path) -> None:
    store = SourceStore(tmp_path, "mdns")
    for i in range(MAX_ADDRESSES_PER_MAC + 3):
        store.record([sighting(A, f"192.0.2.{100 + i}", NOW + i)], now=NOW + i)
    store.record([sighting(B, "192.0.2.50", NOW - RETENTION_SECONDS - 1)], now=NOW)
    document = json.loads(source_file(tmp_path, "mdns").read_text())
    addresses = [o["address"] for o in document["observations"] if o["mac"] == A]
    assert len(addresses) == MAX_ADDRESSES_PER_MAC
    assert addresses[0] == f"192.0.2.{100 + MAX_ADDRESSES_PER_MAC + 2}"
    assert "192.0.2.100" not in addresses
    assert all(o["mac"] != B for o in document["observations"])


def test_store_skips_sightings_that_are_not_ipv4_addresses(tmp_path: Path) -> None:
    store = SourceStore(tmp_path, "router")
    store.record([sighting(A, "fe80::1", NOW), sighting(A, "", NOW), sighting(B, "192.0.2.5", NOW)], now=NOW)
    assert [o.mac for o in AddressInventory.load(tmp_path).observations] == [B]


def test_store_rewrites_the_heartbeat_even_when_nothing_else_changed(tmp_path: Path) -> None:
    store = SourceStore(tmp_path, "arp")
    store.record([sighting(A, "192.0.2.110", NOW)], now=NOW)
    store.record([], now=NOW + 30)
    inventory = AddressInventory.load(tmp_path)
    assert inventory.written_at == {"arp": NOW + 30, "mdns": None, "router": None}
    assert inventory.latest(A, now=NOW + 30, max_age=180) is not None


def test_latest_prefers_arp_then_mdns_then_router_among_fresh_observations(tmp_path: Path) -> None:
    SourceStore(tmp_path, "router").record([sighting(A, "192.0.2.1", NOW - 10)], now=NOW)
    SourceStore(tmp_path, "mdns").record([sighting(A, "192.0.2.2", NOW - 60)], now=NOW)
    SourceStore(tmp_path, "arp").record([sighting(A, "192.0.2.3", NOW - 120)], now=NOW)
    inventory = AddressInventory.load(tmp_path)
    assert inventory.latest(A, now=NOW, max_age=180).address == "192.0.2.3"  # type: ignore[union-attr]
    # Once the ARP observation is too old, the next source in line wins, even though the router's is
    # the freshest of all: freshness is a gate, precedence picks among those that pass it.
    assert inventory.latest(A, now=NOW, max_age=90).address == "192.0.2.2"  # type: ignore[union-attr]
    assert inventory.latest(A, now=NOW, max_age=30).address == "192.0.2.1"  # type: ignore[union-attr]


def test_latest_is_none_when_no_source_is_fresh_and_age_still_says_when(tmp_path: Path) -> None:
    SourceStore(tmp_path, "router").record([sighting(A, "192.0.2.1", NOW - 400)], now=NOW - 400)
    SourceStore(tmp_path, "arp").record([sighting(A, "192.0.2.1", NOW - 300)], now=NOW - 300)
    inventory = AddressInventory.load(tmp_path)
    assert inventory.latest(A, now=NOW, max_age=180) is None
    assert inventory.age(A, now=NOW) == 300.0
    assert inventory.age(B, now=NOW) is None
    assert inventory.latest(B, now=NOW, max_age=180) is None


def test_latest_within_one_source_takes_that_sources_newest_observation(tmp_path: Path) -> None:
    store = SourceStore(tmp_path, "arp")
    store.record([sighting(A, "192.0.2.110", NOW - 100)], now=NOW - 100)
    store.record([sighting(A, "192.0.2.111", NOW - 20)], now=NOW - 20)
    assert AddressInventory.load(tmp_path).latest(A, now=NOW, max_age=180).address == "192.0.2.111"  # type: ignore[union-attr]


def test_role_and_name_come_from_any_source_while_the_address_comes_from_the_freshest(tmp_path: Path) -> None:
    SourceStore(tmp_path, "router").record(
        [sighting(ROUTER, "192.0.2.1", NOW - 200, name="test-router", role="router")], now=NOW - 200
    )
    SourceStore(tmp_path, "arp").record([sighting(ROUTER, "192.0.2.1", NOW - 5)], now=NOW)
    inventory = AddressInventory.load(tmp_path)
    assert inventory.role(ROUTER) == "router"
    assert inventory.name(ROUTER) == "test-router"
    assert inventory.role(A) == "client"
    infrastructure = inventory.infrastructure(now=NOW, max_age=180)
    assert [(mac, o.source, o.address) for mac, o in infrastructure] == [(ROUTER, "arp", "192.0.2.1")]


def test_missing_directory_is_an_empty_inventory(tmp_path: Path) -> None:
    inventory = AddressInventory.load(tmp_path / "nowhere")
    assert inventory.observations == ()
    assert inventory.written_at == {"arp": None, "mdns": None, "router": None}


def test_corrupt_file_is_reported_once_and_read_as_empty_until_it_recovers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source_file(tmp_path, "arp").write_text("not json")
    SourceStore(tmp_path, "mdns").record([sighting(A, "192.0.2.2", NOW)], now=NOW)
    problems = ProblemLog(logging.getLogger("test.inventory"))
    with caplog.at_level(logging.INFO, logger="test.inventory"):
        first = AddressInventory.load(tmp_path, problems=problems)
        second = AddressInventory.load(tmp_path, problems=problems)
        assert [record.levelname for record in caplog.records] == ["WARNING"]
        assert "arp.json" in caplog.text
        assert first.latest(A, now=NOW, max_age=180).source == "mdns"  # type: ignore[union-attr]
        assert second.written_at["arp"] is None
        SourceStore(tmp_path, "arp").record([sighting(A, "192.0.2.3", NOW)], now=NOW)
        third = AddressInventory.load(tmp_path, problems=problems)
        assert [record.levelname for record in caplog.records] == ["WARNING", "INFO"]
        assert third.latest(A, now=NOW, max_age=180).source == "arp"  # type: ignore[union-attr]


def test_unusable_entries_are_skipped_without_losing_the_rest(tmp_path: Path) -> None:
    source_file(tmp_path, "router").write_text(
        json.dumps(
            {
                "source": "router",
                "written_at": NOW,
                "observations": [
                    {"mac": "not-a-mac", "address": "192.0.2.9", "first_seen": NOW, "last_seen": NOW},
                    {"mac": A, "address": "2001:db8::1", "first_seen": NOW, "last_seen": NOW},
                    {"mac": A, "address": "192.0.2.10", "first_seen": NOW, "last_seen": "soon"},
                    {"mac": B, "address": "192.0.2.11", "first_seen": NOW, "last_seen": NOW, "role": "odd"},
                ],
            }
        )
    )
    inventory = AddressInventory.load(tmp_path)
    assert [(o.mac, o.address, o.role) for o in inventory.observations] == [(B, "192.0.2.11", "client")]


def test_problem_log_speaks_once_per_change_and_once_on_recovery(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.problems")
    problems = ProblemLog(logger)
    with caplog.at_level(logging.INFO, logger="test.problems"):
        problems.clear("k", "fine again")
        problems.problem("k", "broken")
        problems.problem("k", "broken")
        problems.problem("k", "broken differently")
        problems.clear("k", "fine again")
        problems.clear("k", "fine again")
    assert [(r.levelname, r.message) for r in caplog.records] == [
        ("WARNING", "broken"),
        ("WARNING", "broken differently"),
        ("INFO", "fine again"),
    ]
    assert problems.active("k") is False
