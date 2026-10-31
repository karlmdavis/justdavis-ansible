"""Tests for the Hue collector's metric families."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder
from justdavis_monitoring_exporters.hue.collector import ZIGBEE_STATUSES, HueCollector
from justdavis_monitoring_exporters.hue.models import HueSnapshot
from justdavis_monitoring_exporters.hue.parser import parse_config, parse_resources

FIXTURES = Path(__file__).parent / "fixtures"
DEVICES, LIGHTS, SENSORS = parse_resources((FIXTURES / "hue_clip_v2_resource.json").read_bytes())
SNAPSHOT = HueSnapshot(
    parse_config((FIXTURES / "hue_api_v1_config.json").read_bytes()), DEVICES, LIGHTS, SENSORS
)
LAMP_B = {"device_id": "00000000-0000-4000-8000-000000000012", "name": "Lamp B"}
DIMMER_A = {"device_id": "00000000-0000-4000-8000-000000000015", "name": "Dimmer A"}
SENSOR_A = {"device_id": "00000000-0000-4000-8000-000000000014", "name": "Motion Sensor A"}


def registry_with(snapshot: HueSnapshot | None) -> CollectorRegistry:
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial())
    collector = HueCollector(statuses)
    if snapshot is not None:
        collector.publish(snapshot, address="192.0.2.21")
    registry = CollectorRegistry()
    registry.register(collector)
    return registry


def test_bridge_families() -> None:
    registry = registry_with(SNAPSHOT)
    assert (
        registry.get_sample_value(
            "hue_bridge_info",
            {
                "bridge_id": "020000FFFE000030",
                "model": "BSB002",
                "swversion": "1978545030",
                "apiversion": "1.78.0",
            },
        )
        == 1.0
    )
    assert (
        registry.get_sample_value(
            "hue_bridge_address_info", {"bridge_id": "020000FFFE000030", "address": "192.0.2.21"}
        )
        == 1.0
    )
    assert registry.get_sample_value("hue_bridge_zigbee_channel") == 25.0
    assert registry.get_sample_value("hue_bridge_internet_connected", {"service": "internet"}) == 1.0
    assert registry.get_sample_value("hue_bridge_internet_connected", {"service": "swupdate"}) == 0.0
    assert (
        registry.get_sample_value("hue_bridge_last_software_install_timestamp_seconds")
        == datetime(2026, 10, 20, 7, 10, tzinfo=UTC).timestamp()
    )


def test_zigbee_connectivity_is_one_hot_over_every_status_with_unknown_for_a_new_one() -> None:
    registry = registry_with(SNAPSHOT)
    for status in ZIGBEE_STATUSES:
        assert registry.get_sample_value("hue_zigbee_connectivity", {**LAMP_B, "status": status}) == float(
            status == "disconnected"
        )
        assert registry.get_sample_value("hue_zigbee_connectivity", {**DIMMER_A, "status": status}) == float(
            status == "unknown"
        )
    bridge = {"device_id": "00000000-0000-4000-8000-000000000010", "name": "Hue Bridge"}
    assert registry.get_sample_value("hue_zigbee_connectivity", {**bridge, "status": "connected"}) is None
    assert (
        registry.get_sample_value(
            "hue_device_info",
            {
                **LAMP_B,
                "product": "Hue white lamp",
                "model": "LWA001",
                "software_version": "1.122.2",
                "room": "",
            },
        )
        == 1.0
    )
    assert registry.get_sample_value("hue_device_battery_ratio", DIMMER_A) == pytest.approx(0.12)
    assert registry.get_sample_value("hue_device_battery_ratio", LAMP_B) is None


def test_lights_and_sensors() -> None:
    registry = registry_with(SNAPSHOT)
    lamp_a = {
        "light_id": "00000000-0000-4000-8000-000000000021",
        "device_id": "00000000-0000-4000-8000-000000000011",
        "name": "Lamp A",
    }
    plug_a = {
        "light_id": "00000000-0000-4000-8000-000000000023",
        "device_id": "00000000-0000-4000-8000-000000000013",
        "name": "Plug A",
    }
    assert registry.get_sample_value("hue_light_on", lamp_a) == 1.0
    assert registry.get_sample_value("hue_light_brightness_ratio", lamp_a) == pytest.approx(0.755)
    assert registry.get_sample_value("hue_light_on", plug_a) == 1.0
    assert registry.get_sample_value("hue_light_brightness_ratio", plug_a) is None
    assert registry.get_sample_value("hue_sensor_temperature_celsius", SENSOR_A) == pytest.approx(19.66)
    assert registry.get_sample_value("hue_sensor_light_level_raw", SENSOR_A) == 30001.0
    assert registry.get_sample_value("hue_sensor_light_level_lux", SENSOR_A) == pytest.approx(
        1000.0, rel=1e-6
    )
    assert registry.get_sample_value("hue_sensor_motion", SENSOR_A) == 1.0


def test_without_a_snapshot_only_the_status_families_are_served() -> None:
    registry = registry_with(None)
    assert registry.get_sample_value("hue_up") == 0.0
    assert registry.get_sample_value("hue_bridge_zigbee_channel") is None
