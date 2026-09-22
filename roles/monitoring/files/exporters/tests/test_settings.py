"""Tests for environment-derived settings."""

import json

import pytest

from justdavis_monitoring_exporters.common.settings import (
    SettingsError,
    TrackedClient,
    env_host,
    env_int,
    env_str,
    parse_static_targets,
    parse_tracked_clients,
)


def test_parse_tracked_clients_normalises_and_defaults_airplay_name() -> None:
    raw = json.dumps([{"mac": "AA:BB:CC:DD:EE:01", "name": "Kitchen", "kind": "homepod"}])
    clients = parse_tracked_clients(raw)
    assert clients == (
        TrackedClient(mac="aa:bb:cc:dd:ee:01", name="Kitchen", kind="homepod", airplay_name="Kitchen"),
    )


def test_parse_tracked_clients_honours_explicit_airplay_name() -> None:
    raw = json.dumps(
        [
            {
                "mac": "aa:bb:cc:dd:ee:01",
                "name": "Living-Room",
                "kind": "homepod",
                "airplay_name": "Living Room",
            }
        ]
    )
    assert parse_tracked_clients(raw)[0].airplay_name == "Living Room"


def test_parse_tracked_clients_empty_string_means_none() -> None:
    assert parse_tracked_clients("") == ()


def test_parse_tracked_clients_rejects_unknown_kind() -> None:
    raw = json.dumps([{"mac": "aa:bb:cc:dd:ee:01", "name": "x", "kind": "toaster"}])
    with pytest.raises(SettingsError) as excinfo:
        parse_tracked_clients(raw)
    assert "kind" in str(excinfo.value)


def test_parse_tracked_clients_rejects_malformed_mac() -> None:
    raw = json.dumps([{"mac": "not-a-mac", "name": "x", "kind": "homepod"}])
    with pytest.raises(SettingsError):
        parse_tracked_clients(raw)


def test_parse_tracked_clients_rejects_invalid_json() -> None:
    with pytest.raises(SettingsError):
        parse_tracked_clients("[not json")


def test_parse_static_targets_accepts_ipv4_list_and_drops_duplicates() -> None:
    assert parse_static_targets(json.dumps(["1.1.1.1", "8.8.8.8", "1.1.1.1"])) == ("1.1.1.1", "8.8.8.8")


def test_parse_static_targets_rejects_non_ip() -> None:
    with pytest.raises(SettingsError):
        parse_static_targets(json.dumps(["example.com"]))


def test_env_host_treats_blank_as_not_configured() -> None:
    assert env_host({"MONITORING_AMPLIFI_HOST": "  "}, "MONITORING_AMPLIFI_HOST") is None
    assert env_host({}, "MONITORING_AMPLIFI_HOST") is None
    assert env_host({"MONITORING_AMPLIFI_HOST": "10.0.0.1"}, "MONITORING_AMPLIFI_HOST") == "10.0.0.1"


def test_env_int_uses_default_and_rejects_garbage() -> None:
    assert env_int({}, "X", 30) == 30
    assert env_int({"X": "45"}, "X", 30) == 45
    with pytest.raises(SettingsError):
        env_int({"X": "soon"}, "X", 30)


def test_env_str_uses_default() -> None:
    assert env_str({}, "X", "d") == "d"
    assert env_str({"X": "v"}, "X", "d") == "v"


def test_env_int_enforces_bounds() -> None:
    assert env_int({"X": "1"}, "X", 30, minimum=1) == 1
    with pytest.raises(SettingsError):
        env_int({"X": "0"}, "X", 30, minimum=1)
    with pytest.raises(SettingsError):
        env_int({"X": "70000"}, "X", 9801, minimum=1, maximum=65535)


def test_parse_tracked_clients_rejects_duplicate_macs() -> None:
    raw = json.dumps(
        [
            {"mac": "aa:bb:cc:dd:ee:01", "name": "One", "kind": "homepod"},
            {"mac": "AA:BB:CC:DD:EE:01", "name": "Two", "kind": "ipad"},
        ]
    )
    with pytest.raises(SettingsError) as excinfo:
        parse_tracked_clients(raw)
    assert "duplicate" in str(excinfo.value)


def test_tracked_client_rejects_blank_name_and_bad_mac_at_construction() -> None:
    with pytest.raises(SettingsError):
        TrackedClient(mac="aa:bb:cc:dd:ee:01", name="", kind="homepod", airplay_name="x")
    with pytest.raises(SettingsError):
        TrackedClient(mac="not-a-mac", name="x", kind="homepod", airplay_name="x")
