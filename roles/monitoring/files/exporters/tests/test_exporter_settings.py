"""Tests for the per-exporter settings objects built from the environment."""

import json

import pytest

from justdavis_monitoring_exporters.airplay.main import AirplaySettings
from justdavis_monitoring_exporters.amplifi.main import AmplifiSettings
from justdavis_monitoring_exporters.common.settings import SettingsError, parse_fingerprint
from justdavis_monitoring_exporters.gateway.main import GatewayDevice, GatewaySettings, UnpinnedGateway
from justdavis_monitoring_exporters.hue.main import HueSettings
from justdavis_monitoring_exporters.lan_discovery.main import LanDiscoverySettings

TRACKED_JSON = json.dumps([{"mac": "02:00:00:00:00:10", "name": "Speaker A", "kind": "homepod"}])


def test_amplifi_settings_defaults_and_not_configured() -> None:
    settings = AmplifiSettings.from_env({})
    assert settings.device is None
    assert settings.port == 9801
    assert settings.listen_addr == "0.0.0.0"
    assert settings.interval_seconds == 30
    assert settings.tracked_clients == ()
    assert settings.shared_dir.name == "shared"


def test_amplifi_settings_full() -> None:
    settings = AmplifiSettings.from_env(
        {
            "MONITORING_AMPLIFI_HOST": "10.0.0.1",
            "AMPLIFI_PASSWORD": "pw",
            "MONITORING_AMPLIFI_INTERVAL": "45",
            "MONITORING_TRACKED_CLIENTS": TRACKED_JSON,
            "MONITORING_SHARED_DIR": "/tmp/shared",
            "MONITORING_LISTEN_ADDR": "127.0.0.1",
            "MONITORING_AMPLIFI_PORT": "9901",
        }
    )
    assert settings.device is not None
    assert settings.device.base_url == "http://10.0.0.1"
    assert settings.interval_seconds == 45
    assert settings.tracked_clients[0].name == "Speaker A"
    assert str(settings.shared_dir) == "/tmp/shared"
    assert settings.listen_addr == "127.0.0.1"
    assert settings.port == 9901
    assert "pw" not in repr(settings)


def test_amplifi_settings_require_password_when_configured() -> None:
    with pytest.raises(SettingsError):
        AmplifiSettings.from_env({"MONITORING_AMPLIFI_HOST": "10.0.0.1"})


def test_gateway_settings() -> None:
    settings = GatewaySettings.from_env(
        {
            "MONITORING_GATEWAY_HOST": "10.1.10.1",
            "GATEWAY_USERNAME": "admin",
            "GATEWAY_PASSWORD": "pw",
            "MONITORING_GATEWAY_TLS_FINGERPRINT_SHA256": "AB:" * 31 + "AB",
        }
    )
    assert isinstance(settings.device, GatewayDevice)
    assert settings.device.base_url == "https://10.1.10.1"
    assert settings.device.tls_fingerprint_sha256 == "AB:" * 31 + "AB"
    assert settings.interval_seconds == 60
    assert settings.relogin_min_seconds == 300
    assert settings.port == 9802
    assert "pw" not in repr(settings)


def test_gateway_settings_without_fingerprint_yield_an_unpinned_device_that_cannot_be_polled() -> None:
    settings = GatewaySettings.from_env(
        {"MONITORING_GATEWAY_HOST": "10.1.10.1", "GATEWAY_USERNAME": "admin", "GATEWAY_PASSWORD": "pw"}
    )
    assert settings.device == UnpinnedGateway(host="10.1.10.1")


def test_gateway_settings_reject_malformed_fingerprint() -> None:
    with pytest.raises(SettingsError):
        GatewaySettings.from_env(
            {
                "MONITORING_GATEWAY_HOST": "10.1.10.1",
                "GATEWAY_USERNAME": "admin",
                "GATEWAY_PASSWORD": "pw",
                "MONITORING_GATEWAY_TLS_FINGERPRINT_SHA256": "AB:CD",
            }
        )


def test_zero_interval_is_rejected() -> None:
    with pytest.raises(SettingsError):
        AmplifiSettings.from_env({"AMPLIFI_PASSWORD": "pw", "MONITORING_AMPLIFI_INTERVAL": "0"})


def test_airplay_settings() -> None:
    settings = AirplaySettings.from_env(
        {
            "MONITORING_LAN_IP": "10.0.0.2",
            "MONITORING_TRACKED_CLIENTS": TRACKED_JSON,
            "MONITORING_LISTEN_ADDR": "172.31.0.1",
        }
    )
    assert settings.lan_ip == "10.0.0.2"
    assert settings.port == 9803
    assert settings.interval_seconds == 30
    assert settings.resolve_timeout_ms == 3000
    assert settings.listen_addr == "172.31.0.1"


def test_airplay_settings_lan_ip_blank_means_not_configured() -> None:
    assert AirplaySettings.from_env({}).lan_ip is None


def test_gateway_settings_require_both_username_and_password() -> None:
    with pytest.raises(SettingsError):
        GatewaySettings.from_env({"MONITORING_GATEWAY_HOST": "10.1.10.1", "GATEWAY_USERNAME": "admin"})


@pytest.mark.parametrize("spelling", ["AB:" * 31 + "AB", "ab:" * 31 + "ab", "ab" * 32, "AB" * 32])
def test_fingerprint_is_accepted_in_every_common_spelling(spelling: str) -> None:
    assert parse_fingerprint(spelling, "X") == spelling


@pytest.mark.parametrize("spelling", ["AB:CD", "sha256:" + "ab" * 32, "zz" * 32])
def test_fingerprint_rejects_other_forms(spelling: str) -> None:
    with pytest.raises(SettingsError):
        parse_fingerprint(spelling, "X")


def test_lan_discovery_settings_defaults_and_not_configured() -> None:
    settings = LanDiscoverySettings.from_env({})
    assert settings.lan_ip is None
    assert settings.lan_subnet is None
    assert settings.port == 9804
    assert settings.interval_seconds == 30
    assert settings.max_age_seconds == 180
    assert settings.static_ping_targets == ()
    assert settings.ping.interval_seconds == 1


def test_lan_discovery_settings_full() -> None:
    settings = LanDiscoverySettings.from_env(
        {
            "MONITORING_LAN_IP": "192.0.2.2",
            "MONITORING_LAN_SUBNET": "192.0.2.0/24",
            "MONITORING_LAN_DISCOVERY_PORT": "9904",
            "MONITORING_LAN_DISCOVERY_INTERVAL": "45",
            "MONITORING_DEVICE_ADDRESS_MAX_AGE": "300",
            "MONITORING_TRACKED_CLIENTS": TRACKED_JSON,
            "MONITORING_STATIC_PING_TARGETS": '["1.1.1.1"]',
            "MONITORING_PING_HISTORY_SIZE": "120",
        }
    )
    assert settings.lan_ip == "192.0.2.2"
    assert str(settings.lan_subnet) == "192.0.2.0/24"
    assert (settings.port, settings.interval_seconds, settings.max_age_seconds) == (9904, 45, 300)
    assert settings.static_ping_targets == ("1.1.1.1",)
    assert settings.ping.history_size == 120


def test_lan_discovery_settings_reject_a_bad_subnet() -> None:
    with pytest.raises(SettingsError):
        LanDiscoverySettings.from_env({"MONITORING_LAN_SUBNET": "not-a-subnet"})


def test_airplay_settings_carry_the_address_freshness_window() -> None:
    assert AirplaySettings.from_env({}).max_age_seconds == 180
    assert AirplaySettings.from_env({"MONITORING_DEVICE_ADDRESS_MAX_AGE": "60"}).max_age_seconds == 60


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("homepod", True),
        ("appletv", True),
        ("other_infra", True),
        ("ipad", False),
        ("phone", False),
        ("laptop", False),
        ("other", False),
    ],
)
def test_expect_online_follows_from_the_kind(kind: str, expected: bool) -> None:
    from justdavis_monitoring_exporters.common.settings import parse_tracked_clients

    (client,) = parse_tracked_clients(json.dumps([{"mac": "02:00:00:00:00:10", "name": "A", "kind": kind}]))
    assert client.expect_online is expected


def test_hue_settings_defaults_and_not_configured() -> None:
    settings = HueSettings.from_env({})
    assert settings.bridge is None
    assert (settings.port, settings.interval_seconds, settings.max_age_seconds) == (9805, 30, 180)


def test_hue_settings_full_and_key_required() -> None:
    settings = HueSettings.from_env(
        {
            "MONITORING_HUE_BRIDGE_MAC": "02:00:00:00:00:30",
            "HUE_APPLICATION_KEY": "k",
            "MONITORING_HUE_PORT": "9905",
        }
    )
    assert settings.bridge is not None
    assert settings.bridge.bridge_id == "020000fffe000030"
    assert settings.port == 9905
    assert "k" not in repr(settings).replace("key", "")
    with pytest.raises(SettingsError):
        HueSettings.from_env({"MONITORING_HUE_BRIDGE_MAC": "02:00:00:00:00:30"})
    with pytest.raises(SettingsError):
        HueSettings.from_env({"MONITORING_HUE_BRIDGE_MAC": "not-a-mac", "HUE_APPLICATION_KEY": "k"})
