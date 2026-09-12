"""Prometheus collector for the AmpliFi snapshot.

Every metric family is rebuilt from the current snapshot on each scrape, so when the router cannot be
reached the per-device series simply disappear instead of going stale, and label sets from rotating
private MAC addresses never accumulate.
"""

import threading
from collections.abc import Iterator, Sequence

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, InfoMetricFamily, Metric
from prometheus_client.registry import Collector

from justdavis_monitoring_exporters.amplifi.models import AmplifiSnapshot, WifiClient
from justdavis_monitoring_exporters.amplifi.targets import TargetInfo
from justdavis_monitoring_exporters.common.counters import Unwrapper32
from justdavis_monitoring_exporters.common.labels import sanitise_label
from justdavis_monitoring_exporters.common.metrics import status_families
from justdavis_monitoring_exporters.common.settings import TrackedClient
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder

_CLIENT_LABELS = ["mac", "name", "kind"]
_MESH_POINT_LABELS = ["mac", "name"]
_BACKHAUL_NETWORK = "Internal network"
_AIRPLAY_SERVICES = ("_airplay._tcp", "_raop._tcp")
_KBPS = 1000.0


class AmplifiCollector(Collector):
    def __init__(
        self,
        snapshots: SnapshotHolder[AmplifiSnapshot],
        statuses: SnapshotHolder[ScrapeStatus],
        targets: SnapshotHolder[tuple[TargetInfo, ...]],
        tracked: Sequence[TrackedClient],
        unwrapper: Unwrapper32,
    ) -> None:
        self.snapshots = snapshots
        self.statuses = statuses
        self.targets = targets
        self._tracked = {client.mac: client for client in tracked}
        self._unwrapper = unwrapper
        self._unwrap_lock = threading.Lock()

    def collect(self) -> Iterator[Metric]:
        yield from status_families("amplifi", self.statuses.get())
        yield from self._target_families()
        snapshot = self.snapshots.get()
        if snapshot is None:
            return
        yield from self._router_families(snapshot)
        yield from self._mesh_point_families(snapshot)
        yield from self._client_families(snapshot)
        yield from self._airplay_families(snapshot)

    def _target_families(self) -> Iterator[Metric]:
        info = InfoMetricFamily(
            "monitoring_target", "Friendly name and kind for every ping target.", labels=["ip"]
        )
        for target in self.targets.get() or ():
            info.add_metric([target.ip], {"name": sanitise_label(target.name), "kind": target.kind})
        yield info

    @staticmethod
    def _router_families(snapshot: AmplifiSnapshot) -> Iterator[Metric]:
        yield GaugeMetricFamily(
            "amplifi_router_uptime_seconds",
            "Uptime of the AmpliFi router.",
            value=float(snapshot.router.uptime_seconds),
        )
        wan = snapshot.wan_port
        yield GaugeMetricFamily(
            "amplifi_wan_link", "1 when the router's WAN port has link.", value=float(wan.link)
        )
        if wan.rx_bitrate_kbps is not None:
            yield GaugeMetricFamily(
                "amplifi_wan_rx_bits_per_second",
                "Current WAN receive rate.",
                value=wan.rx_bitrate_kbps * _KBPS,
            )
        if wan.tx_bitrate_kbps is not None:
            yield GaugeMetricFamily(
                "amplifi_wan_tx_bits_per_second",
                "Current WAN transmit rate.",
                value=wan.tx_bitrate_kbps * _KBPS,
            )

    @staticmethod
    def _mesh_point_families(snapshot: AmplifiSnapshot) -> Iterator[Metric]:
        info = InfoMetricFamily(
            "amplifi_mesh_point", "Mesh point identity and backhaul band.", labels=_MESH_POINT_LABELS
        )
        rssi = GaugeMetricFamily(
            "amplifi_mesh_point_rssi_min_dbm", "Mesh point backhaul minimum RSSI.", labels=_MESH_POINT_LABELS
        )
        uptime = GaugeMetricFamily(
            "amplifi_mesh_point_uptime_seconds", "Mesh point uptime.", labels=_MESH_POINT_LABELS
        )
        for mp in snapshot.mesh_points:
            labels = [mp.mac, sanitise_label(mp.name)]
            info.add_metric(labels, {"backhaul_band": mp.backhaul_band, "platform": mp.platform})
            rssi.add_metric(labels, float(mp.rssi_min_dbm))
            uptime.add_metric(labels, float(mp.uptime_seconds))
        yield info
        yield rssi
        yield uptime

    def _client_labels(self, client: WifiClient) -> list[str]:
        tracked = self._tracked.get(client.mac)
        if tracked is not None:
            return [client.mac, sanitise_label(tracked.name), tracked.kind]
        kind = "backhaul" if client.network == _BACKHAUL_NETWORK else "other"
        return [client.mac, "", kind]

    def _client_families(self, snapshot: AmplifiSnapshot) -> Iterator[Metric]:
        ap_names = {snapshot.router.mac: snapshot.router.name}
        ap_names.update({mp.mac: mp.name for mp in snapshot.mesh_points})
        info = InfoMetricFamily(
            "amplifi_client", "Current WiFi association of a client.", labels=_CLIENT_LABELS
        )
        signal = GaugeMetricFamily(
            "amplifi_client_signal_quality", "Router-reported signal quality (0-100).", labels=_CLIENT_LABELS
        )
        happiness = GaugeMetricFamily(
            "amplifi_client_happiness_score",
            "Router-reported happiness score (0-100).",
            labels=_CLIENT_LABELS,
        )
        rx_rate = GaugeMetricFamily(
            "amplifi_client_rx_bits_per_second", "Negotiated receive rate.", labels=_CLIENT_LABELS
        )
        tx_rate = GaugeMetricFamily(
            "amplifi_client_tx_bits_per_second", "Negotiated transmit rate.", labels=_CLIENT_LABELS
        )
        inactive = GaugeMetricFamily(
            "amplifi_client_inactive_seconds",
            "Seconds since the client last sent traffic.",
            labels=_CLIENT_LABELS,
        )
        rx_bytes = CounterMetricFamily(
            "amplifi_client_rx_bytes",
            "Bytes received by the client (32-bit wraps unwrapped).",
            labels=_CLIENT_LABELS,
        )
        tx_bytes = CounterMetricFamily(
            "amplifi_client_tx_bytes",
            "Bytes transmitted by the client (32-bit wraps unwrapped).",
            labels=_CLIENT_LABELS,
        )
        with self._unwrap_lock:
            self._unwrapper.forget_missing(
                {(client.mac, direction) for client in snapshot.clients for direction in ("rx", "tx")}
            )
            for client in snapshot.clients:
                labels = self._client_labels(client)
                info.add_metric(
                    labels,
                    {
                        "ap": client.ap_mac,
                        "ap_name": sanitise_label(ap_names.get(client.ap_mac, "")),
                        "band": client.band,
                        "network": client.network,
                        "mode": client.mode or "",
                    },
                )
                signal.add_metric(labels, float(client.signal_quality))
                happiness.add_metric(labels, float(client.happiness_score))
                rx_rate.add_metric(labels, client.rx_bitrate_kbps * _KBPS)
                tx_rate.add_metric(labels, client.tx_bitrate_kbps * _KBPS)
                inactive.add_metric(labels, float(client.inactive_seconds))
                rx_bytes.add_metric(
                    labels, float(self._unwrapper.update((client.mac, "rx"), client.rx_bytes))
                )
                tx_bytes.add_metric(
                    labels, float(self._unwrapper.update((client.mac, "tx"), client.tx_bytes))
                )
        yield info
        yield signal
        yield happiness
        yield rx_rate
        yield tx_rate
        yield inactive
        yield rx_bytes
        yield tx_bytes

    def _airplay_families(self, snapshot: AmplifiSnapshot) -> Iterator[Metric]:
        advertised = GaugeMetricFamily(
            "amplifi_client_airplay_advertised",
            "1 when the router's Bonjour table lists the service for this HomePod.",
            labels=[*_CLIENT_LABELS, "service"],
        )
        for tracked in self._tracked.values():
            if tracked.kind != "homepod":
                continue
            services = snapshot.bonjour.get(tracked.mac, frozenset())
            for service in _AIRPLAY_SERVICES:
                present = f"{service}.local" in services
                advertised.add_metric(
                    [tracked.mac, sanitise_label(tracked.name), tracked.kind, service], float(present)
                )
        yield advertised
