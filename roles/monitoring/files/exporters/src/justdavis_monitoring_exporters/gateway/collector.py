"""Prometheus collector for the Comcast gateway status snapshot."""

from collections.abc import Iterator

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, InfoMetricFamily, Metric
from prometheus_client.registry import Collector

from justdavis_monitoring_exporters.common.labels import sanitise_label
from justdavis_monitoring_exporters.common.metrics import status_families
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder
from justdavis_monitoring_exporters.gateway.models import GatewayStatus

_CHANNEL = ["channel"]


class GatewayCollector(Collector):
    def __init__(
        self, snapshots: SnapshotHolder[GatewayStatus], statuses: SnapshotHolder[ScrapeStatus]
    ) -> None:
        self.snapshots = snapshots
        self.statuses = statuses

    def publish(self, snapshot: GatewayStatus) -> None:
        self.snapshots.set(snapshot)

    def clear(self) -> None:
        self.snapshots.clear()

    def collect(self) -> Iterator[Metric]:
        yield from status_families("gateway", self.statuses.get())
        snapshot = self.snapshots.get()
        if snapshot is None:
            return
        yield GaugeMetricFamily(
            "gateway_uptime_seconds", "Gateway system uptime.", value=float(snapshot.uptime_seconds)
        )
        yield GaugeMetricFamily(
            "gateway_internet_active",
            "1 when the gateway reports Internet: Active.",
            value=float(snapshot.internet_active),
        )
        wan = InfoMetricFamily("gateway_wan", "The gateway's WAN addresses as it reports them.")
        wan.add_metric(
            [],
            {
                "wan_ip": sanitise_label(snapshot.wan_ip),
                "wan_static_ip": sanitise_label(snapshot.wan_static_ip),
                "isp_gateway": sanitise_label(snapshot.isp_gateway),
            },
        )
        yield wan
        info = InfoMetricFamily(
            "gateway_docsis_downstream", "Downstream channel frequency and modulation.", labels=_CHANNEL
        )
        locked = GaugeMetricFamily(
            "gateway_docsis_downstream_locked", "1 when the channel is locked.", labels=_CHANNEL
        )
        snr = GaugeMetricFamily("gateway_docsis_downstream_snr_db", "Downstream SNR / MER.", labels=_CHANNEL)
        power = GaugeMetricFamily(
            "gateway_docsis_downstream_power_dbmv", "Downstream receive power.", labels=_CHANNEL
        )
        unerrored = CounterMetricFamily(
            "gateway_docsis_downstream_unerrored_codewords",
            "Codewords received without errors.",
            labels=_CHANNEL,
        )
        correctable = CounterMetricFamily(
            "gateway_docsis_downstream_correctable_codewords",
            "Codewords with corrected errors.",
            labels=_CHANNEL,
        )
        uncorrectable = CounterMetricFamily(
            "gateway_docsis_downstream_uncorrectable_codewords",
            "Codewords with uncorrectable errors.",
            labels=_CHANNEL,
        )
        for channel in snapshot.downstream:
            labels = [str(channel.index)]
            info.add_metric(
                labels,
                {
                    "frequency_hz": "" if channel.frequency_hz is None else str(channel.frequency_hz),
                    "modulation": sanitise_label(channel.modulation),
                },
            )
            locked.add_metric(labels, float(channel.locked))
            if channel.snr_db is not None:
                snr.add_metric(labels, channel.snr_db)
            if channel.power_dbmv is not None:
                power.add_metric(labels, channel.power_dbmv)
            if channel.unerrored is not None:
                unerrored.add_metric(labels, float(channel.unerrored))
            if channel.correctable is not None:
                correctable.add_metric(labels, float(channel.correctable))
            if channel.uncorrectable is not None:
                uncorrectable.add_metric(labels, float(channel.uncorrectable))
        yield info
        yield locked
        yield snr
        yield power
        yield unerrored
        yield correctable
        yield uncorrectable
