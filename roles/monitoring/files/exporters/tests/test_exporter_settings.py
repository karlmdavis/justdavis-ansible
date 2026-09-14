"""Tests for the per-exporter settings objects built from the environment."""

import json

import pytest

from justdavis_monitoring_exporters.airplay.main import AirplaySettings
from justdavis_monitoring_exporters.amplifi.main import AmplifiSettings
from justdavis_monitoring_exporters.common.settings import SettingsError
from justdavis_monitoring_exporters.gateway.main import GatewaySettings

TRACKED_JSON = json.dumps([{"mac": "02:00:00:00:00:10", "name": "Speaker A", "kind": "homepod"}])


def test_amplifi_settings_defaults_and_not_configured() -> None:
    settings = AmplifiSettings.from_env({"AMPLIFI_PASSWORD": "pw"})
    assert settings.host is None
    assert settings.port == 9801
    assert settings.listen_addr == "0.0.0.0"
    assert settings.interval_seconds == 30
    assert settings.tracked_clients == ()
    assert settings.static_ping_targets == ()
    assert settings.shared_dir.name == "shared"


def test_amplifi_settings_full() -> None:
    settings = AmplifiSettings.from_env(
        {
            "MONITORING_AMPLIFI_HOST": "10.0.0.1",
            "AMPLIFI_PASSWORD": "pw",
            "MONITORING_AMPLIFI_INTERVAL": "45",
            "MONITORING_TRACKED_CLIENTS": TRACKED_JSON,
            "MONITORING_STATIC_PING_TARGETS": '["1.1.1.1"]',
            "MONITORING_SHARED_DIR": "/tmp/shared",
            "MONITORING_LISTEN_ADDR": "127.0.0.1",
            "MONITORING_AMPLIFI_PORT": "9901",
        }
    )
    assert settings.host == "10.0.0.1"
    assert settings.base_url == "http://10.0.0.1"
    assert settings.interval_seconds == 45
    assert settings.tracked_clients[0].name == "Speaker A"
    assert settings.static_ping_targets == ("1.1.1.1",)
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
    assert settings.base_url == "https://10.1.10.1"
    assert settings.tls_fingerprint_sha256 == "AB:" * 31 + "AB"
    assert settings.interval_seconds == 60
    assert settings.relogin_min_seconds == 300
    assert settings.port == 9802
    assert "pw" not in repr(settings)


def test_gateway_settings_without_fingerprint_is_not_configured_and_never_plain_http() -> None:
    settings = GatewaySettings.from_env(
        {"MONITORING_GATEWAY_HOST": "10.1.10.1", "GATEWAY_USERNAME": "admin", "GATEWAY_PASSWORD": "pw"}
    )
    assert settings.tls_fingerprint_sha256 is None
    assert settings.configured is False
    assert settings.base_url == "https://10.1.10.1"


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
