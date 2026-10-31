"""Tests for HomeKit accessory presence read from `_hap._tcp` announcements."""

import logging

import pytest
from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.lan_discovery.homekit import (
    HomekitCollector,
    HomekitTracker,
    accessory_from_instance,
)
from justdavis_monitoring_exporters.lan_discovery.mdns import CachedInstance

HAP = "_hap._tcp.local."
NOW = 1_000_000.0


def hap(
    instance: str, txt: dict[str, str], *, seen_at: float = NOW, address: str = "192.0.2.50"
) -> CachedInstance:
    return CachedInstance(
        "_hap._tcp", instance, f"{instance}.{HAP}", "accessory-a.local.", txt, ((address, seen_at),), seen_at
    )


def test_accessory_fields_come_from_the_txt_record() -> None:
    accessory = accessory_from_instance(
        hap(
            "Accessory A",
            {"id": "02:00:00:00:00:40", "md": "Model A", "ci": "5", "sf": "0", "c#": "149", "s#": "7"},
        ),
        present=True,
    )
    assert accessory is not None
    assert (accessory.identity, accessory.model, accessory.category) == ("02:00:00:00:00:40", "Model A", "5")
    assert (accessory.paired, accessory.config_number, accessory.last_seen, accessory.addresses) == (
        True,
        149,
        NOW,
        ("192.0.2.50",),
    )
    unpaired = accessory_from_instance(hap("Accessory B", {"sf": "1", "c#": "x"}), present=True)
    assert unpaired is not None and (unpaired.paired, unpaired.config_number) == (False, None)
    bare = accessory_from_instance(hap("Accessory C", {}), present=True)
    assert bare is not None and bare.paired is None
    without_txt = CachedInstance("_hap._tcp", "D", f"D.{HAP}", None, {}, (), None)
    assert accessory_from_instance(without_txt, present=True) is None


def test_tracker_keeps_a_vanished_accessory_as_not_present_until_retention_ends(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracker = HomekitTracker(retention_seconds=3600, max_accessories=2)
    first = tracker.refresh([hap("A", {"sf": "0"}), hap("B", {"sf": "1"})], now=NOW)
    assert [(a.instance, a.present, a.paired) for a in first] == [("A", True, True), ("B", True, False)]
    second = tracker.refresh([hap("A", {"sf": "0"}, seen_at=NOW + 100)], now=NOW + 100)
    assert [(a.instance, a.present, a.last_seen) for a in second] == [
        ("A", True, NOW + 100),
        ("B", False, NOW),
    ]
    third = tracker.refresh([hap("A", {"sf": "0"}, seen_at=NOW + 100)], now=NOW + 3700)
    assert [a.instance for a in third] == ["A"]
    with caplog.at_level(logging.WARNING):
        capped = tracker.refresh(
            [
                hap("A", {}, seen_at=NOW + 3700),
                hap("C", {}, seen_at=NOW + 3701),
                hap("D", {}, seen_at=NOW + 3702),
            ],
            now=NOW + 3702,
        )
    assert [a.instance for a in capped] == ["C", "D"]
    assert "more than 2" in caplog.text


def test_collector_labels_the_accessory_never_the_instance() -> None:
    tracker = HomekitTracker()
    tracker.refresh(
        [
            hap(
                "Accessory " + chr(0x2019) + "A",
                {"id": "02:00:00:00:00:40", "md": "Model A", "ci": "5", "sf": "1", "c#": "3"},
            )
        ],
        now=NOW,
    )
    registry = CollectorRegistry()
    registry.register(HomekitCollector(tracker))
    a = {"accessory": "Accessory 'A"}
    assert (
        registry.get_sample_value(
            "homekit_accessory_info", {**a, "id": "02:00:00:00:00:40", "model": "Model A", "category": "5"}
        )
        == 1.0
    )
    assert registry.get_sample_value("homekit_accessory_present", a) == 1.0
    assert registry.get_sample_value("homekit_accessory_paired", a) == 0.0
    assert registry.get_sample_value("homekit_accessory_config_number", a) == 3.0
    assert registry.get_sample_value("homekit_accessory_last_seen_timestamp_seconds", a) == NOW
    assert registry.get_sample_value("homekit_accessory_address_info", {**a, "address": "192.0.2.50"}) == 1.0


def test_the_cap_evicts_vanished_accessories_before_present_ones() -> None:
    tracker = HomekitTracker(max_accessories=2)
    tracker.refresh([hap("Old", {}, seen_at=NOW - 5000)], now=NOW - 5000)
    # "Old" has vanished (not in this refresh); two fresh guests arrive while the household's own
    # accessory, heard long ago but still present, must stay.
    kept = tracker.refresh([hap("Household", {}, seen_at=NOW - 4000), hap("Guest", {}, seen_at=NOW)], now=NOW)
    assert [a.instance for a in kept] == ["Guest", "Household"]
