"""Tests for the Comcast gateway Prometheus collector."""

from pathlib import Path

from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder
from justdavis_monitoring_exporters.gateway.collector import GatewayCollector
from justdavis_monitoring_exporters.gateway.models import GatewayStatus
from justdavis_monitoring_exporters.gateway.parser import parse_comcast_network

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = parse_comcast_network((FIXTURES / "gateway_comcast_network.html").read_text())


def registry_with(snapshot: GatewayStatus | None) -> CollectorRegistry:
    snapshots: SnapshotHolder[GatewayStatus] = SnapshotHolder()
    if snapshot is not None:
        snapshots.set(snapshot)
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial().succeeded(timestamp=1.0, duration_seconds=0.1))
    registry = CollectorRegistry()
    registry.register(GatewayCollector(snapshots, statuses))
    return registry


def test_status_only_without_snapshot() -> None:
    registry = registry_with(None)
    assert registry.get_sample_value("gateway_up") == 1.0
    assert registry.get_sample_value("gateway_uptime_seconds") is None


def test_uptime_and_internet_state() -> None:
    registry = registry_with(SNAPSHOT)
    assert registry.get_sample_value("gateway_uptime_seconds") == 6149.0
    assert registry.get_sample_value("gateway_internet_active") == 1.0


def test_downstream_channel_gauges_keyed_by_channel_only() -> None:
    registry = registry_with(SNAPSHOT)
    assert registry.get_sample_value("gateway_docsis_downstream_snr_db", {"channel": "1"}) == 44.0
    assert registry.get_sample_value("gateway_docsis_downstream_power_dbmv", {"channel": "1"}) == 10.6
    assert registry.get_sample_value("gateway_docsis_downstream_locked", {"channel": "34"}) == 1.0


def test_downstream_channel_info_carries_frequency_and_modulation() -> None:
    registry = registry_with(SNAPSHOT)
    labels = {"channel": "34", "frequency_hz": "957000000", "modulation": "OFDM"}
    assert registry.get_sample_value("gateway_docsis_downstream_info", labels) == 1.0


def test_codeword_counters() -> None:
    registry = registry_with(SNAPSHOT)
    ch1 = {"channel": "1"}
    assert (
        registry.get_sample_value("gateway_docsis_downstream_unerrored_codewords_total", ch1) == 209921024.0
    )
    assert (
        registry.get_sample_value("gateway_docsis_downstream_correctable_codewords_total", ch1) == 182816061.0
    )
    assert registry.get_sample_value("gateway_docsis_downstream_uncorrectable_codewords_total", ch1) == 0.0
