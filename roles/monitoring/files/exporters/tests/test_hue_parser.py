"""Tests for the Hue bridge parsers: the CLIP v2 resource list and the v1 configuration."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from justdavis_monitoring_exporters.common.errors import LoginError, ParseError
from justdavis_monitoring_exporters.hue.parser import parse_config, parse_resources

FIXTURES = Path(__file__).parent / "fixtures"
RESOURCES = (FIXTURES / "hue_clip_v2_resource.json").read_bytes()
CONFIG = (FIXTURES / "hue_api_v1_config.json").read_bytes()
PUBLIC_CONFIG = (FIXTURES / "hue_api_v1_config_unauthenticated.json").read_bytes()
LAMP_A = "00000000-0000-4000-8000-000000000011"
LAMP_B = "00000000-0000-4000-8000-000000000012"
PLUG_A = "00000000-0000-4000-8000-000000000013"
SENSOR_A = "00000000-0000-4000-8000-000000000014"
DIMMER_A = "00000000-0000-4000-8000-000000000015"


def test_parses_devices_with_zigbee_status_battery_and_room() -> None:
    devices, _lights, _sensors = parse_resources(RESOURCES)
    by_id = {d.device_id: d for d in devices}
    assert len(by_id) == 6
    lamp_a = by_id[LAMP_A]
    assert (lamp_a.name, lamp_a.product, lamp_a.model, lamp_a.software_version) == (
        "Lamp A",
        "Hue color lamp",
        "LCA001",
        "1.122.2",
    )
    assert (lamp_a.room, lamp_a.zigbee_status, lamp_a.zigbee_mac) == (
        "Room A",
        "connected",
        "02:00:00:00:00:00:00:31",
    )
    assert lamp_a.battery_ratio is None
    assert by_id[LAMP_B].zigbee_status == "disconnected"
    assert by_id[LAMP_B].room == ""
    assert by_id[SENSOR_A].battery_ratio == pytest.approx(0.86)
    assert by_id[DIMMER_A].zigbee_status == "some_future_state"
    bridge = by_id["00000000-0000-4000-8000-000000000010"]
    assert (bridge.zigbee_status, bridge.zigbee_mac) == (None, None)


def test_parses_lights_with_brightness_as_a_ratio_and_none_for_a_plug() -> None:
    _devices, lights, _sensors = parse_resources(RESOURCES)
    by_device = {light.device_id: light for light in lights}
    assert by_device[LAMP_A].on is True
    assert by_device[LAMP_A].brightness_ratio == pytest.approx(0.755)
    assert by_device[LAMP_B].on is False
    assert by_device[LAMP_B].brightness_ratio == pytest.approx(1.0)
    assert (by_device[PLUG_A].on, by_device[PLUG_A].brightness_ratio, by_device[PLUG_A].name) == (
        True,
        None,
        "Plug A",
    )


def test_sensor_readings_prefer_the_report_sub_object() -> None:
    _devices, _lights, sensors = parse_resources(RESOURCES)
    (sensor,) = sensors
    assert sensor.device_id == SENSOR_A
    assert sensor.name == "Motion Sensor A"
    assert sensor.temperature_celsius == pytest.approx(19.66)
    assert sensor.light_level_raw == 30001
    assert sensor.motion is True


def test_sensor_readings_fall_back_to_the_older_fields() -> None:
    data = json.loads(RESOURCES)
    for resource in data["data"]:
        for section in ("temperature", "light", "motion"):
            if isinstance(resource.get(section), dict):
                resource[section] = {k: v for k, v in resource[section].items() if not k.endswith("_report")}
    _devices, _lights, (sensor,) = parse_resources(json.dumps(data).encode())
    assert (sensor.temperature_celsius, sensor.light_level_raw, sensor.motion) == (19.5, 20000, False)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("data", 1, "metadata"), None),
        (("data", 7, "owner", "rid"), None),
        (("data", 7, "on", "on"), "yes"),
        (("data", 10, "status"), 7),
        (("data",), None),
    ],
    ids=[
        "device without metadata",
        "light without owner",
        "on is not a bool",
        "status is not a string",
        "no data",
    ],
)
def test_a_changed_resource_list_fails_naming_the_path(path: tuple[object, ...], value: object) -> None:
    data = json.loads(RESOURCES)
    node = data
    for key in path[:-1]:
        node = node[key]
    if value is None:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    with pytest.raises(ParseError) as raised:
        parse_resources(json.dumps(data).encode())
    assert "resources" in str(raised.value)
    # Values from the bridge never appear in the message.
    assert "Lamp" not in str(raised.value)


def test_resource_list_with_errors_does_not_parse() -> None:
    data = json.loads(RESOURCES)
    data["errors"] = [{"description": "something"}]
    with pytest.raises(ParseError, match="reported 1 error"):
        parse_resources(json.dumps(data).encode())


def test_parses_the_configuration() -> None:
    config = parse_config(CONFIG)
    assert (config.bridge_id, config.model, config.swversion, config.apiversion) == (
        "020000FFFE000030",
        "BSB002",
        "1978545030",
        "1.78.0",
    )
    assert config.zigbee_channel == 25
    assert config.utc == datetime(2026, 10, 31, 20, 0, 5, tzinfo=UTC).timestamp()
    assert config.internet_services == {
        "internet": True,
        "remoteaccess": True,
        "time": True,
        "swupdate": False,
    }
    assert config.last_install == datetime(2026, 10, 20, 7, 10, 0, tzinfo=UTC).timestamp()


def test_public_configuration_means_the_key_was_rejected() -> None:
    with pytest.raises(LoginError):
        parse_config(PUBLIC_CONFIG)


def test_configuration_without_an_install_or_a_clock_still_parses() -> None:
    data = json.loads(CONFIG)
    del data["swupdate2"]["bridge"]["lastinstall"]
    del data["UTC"]
    config = parse_config(json.dumps(data).encode())
    assert (config.last_install, config.utc) == (None, None)
    data["swupdate2"]["bridge"]["lastinstall"] = "none"
    assert parse_config(json.dumps(data).encode()).last_install is None


def test_malformed_configuration_fails_naming_the_path() -> None:
    data = json.loads(CONFIG)
    data["zigbeechannel"] = "twenty-five"
    with pytest.raises(ParseError, match=r"config\.zigbeechannel"):
        parse_config(json.dumps(data).encode())
    with pytest.raises(ParseError, match="not valid JSON"):
        parse_config(b"<html>")
