"""Prometheus collector for the AmpliFi snapshot.

The scrape thread hands each successful poll to `publish()`, which resolves duplicate associations,
advances the byte-counter unwrapping (so every poll is observed, however often Prometheus scrapes), and
stores an immutable published snapshot. `collect()` only reads it. Every `publish()` replaces the whole
snapshot, so label sets from rotating private MAC addresses never accumulate; when the router cannot be
reached the scraper calls `clear()`, so per-device series disappear instead of going stale. Every label
value that comes from the network goes through `sanitise_label`.
"""

import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, InfoMetricFamily, Metric
from prometheus_client.registry import Collector

from justdavis_monitoring_exporters.amplifi.models import AmplifiSnapshot, WifiClient
from justdavis_monitoring_exporters.amplifi.targets import TargetInfo
from justdavis_monitoring_exporters.common.counters import Unwrapper32
from justdavis_monitoring_exporters.common.labels import sanitise_label
from justdavis_monitoring_exporters.common.mdns import AIRPLAY_SERVICES, short_service
from justdavis_monitoring_exporters.common.metrics import status_families
from justdavis_monitoring_exporters.common.settings import ClientKind, TrackedClient
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder

_CLIENT_LABELS = ["mac", "name", "kind"]
_TRACKED_LABELS = ["mac", "name", "kind"]
_MESH_POINT_LABELS = ["mac", "name"]
_KBPS = 1000.0

type Direction = Literal["rx", "tx"]
# Untracked clients are "other", and mesh backhaul links get their own kind.
type ClientLabelKind = ClientKind | Literal["backhaul"]
# The router's byte counters belong to an association, so a client that roams to another access
# point gets a fresh baseline rather than a spurious 4 GiB "wrap".
type CounterKey = tuple[str, str, Direction]


@dataclass(frozen=True, slots=True)
class PublishedSnapshot:
    """A snapshot with duplicate associations resolved and byte counters unwrapped."""

    snapshot: AmplifiSnapshot
    clients: tuple[WifiClient, ...]
    rx_bytes_total: Mapping[str, int]
    tx_bytes_total: Mapping[str, int]


def dedupe_clients(clients: Sequence[WifiClient]) -> tuple[WifiClient, ...]:
    """Keep one association per MAC: the most recently active one (lowest `inactive_seconds`)."""
    best: dict[str, WifiClient] = {}
    for client in clients:
        current = best.get(client.mac)
        if current is None or client.inactive_seconds < current.inactive_seconds:
            best[client.mac] = client
    return tuple(best.values())


class AmplifiCollector(Collector):
    def __init__(
        self,
        statuses: SnapshotHolder[ScrapeStatus],
        tracked: Sequence[TrackedClient],
        unwrapper: Unwrapper32[CounterKey],
    ) -> None:
        self.statuses = statuses
        self._published: SnapshotHolder[PublishedSnapshot] = SnapshotHolder()
        self._targets: SnapshotHolder[tuple[TargetInfo, ...]] = SnapshotHolder()
        self._tracked = {client.mac: client for client in tracked}
        self._unwrapper = unwrapper
        self._publish_lock = threading.Lock()
        self._target_files_ok: bool | None = None

    def publish(self, snapshot: AmplifiSnapshot) -> None:
        with self._publish_lock:
            clients = dedupe_clients(snapshot.clients)
            keys: set[CounterKey] = {(c.mac, c.ap_mac, d) for c in clients for d in ("rx", "tx")}
            self._unwrapper.forget_missing(keys)
            rx = {c.mac: self._unwrapper.update((c.mac, c.ap_mac, "rx"), c.rx_bytes) for c in clients}
            tx = {c.mac: self._unwrapper.update((c.mac, c.ap_mac, "tx"), c.tx_bytes) for c in clients}
            self._published.set(PublishedSnapshot(snapshot, clients, rx, tx))

    def clear(self) -> None:
        """Drop the device metrics after a failed poll; counter baselines restart on the next one."""
        with self._publish_lock:
            self._published.clear()
            self._unwrapper.forget_missing(())

    def set_targets(self, targets: tuple[TargetInfo, ...]) -> None:
        self._targets.set(targets)

    def set_target_files_ok(self, ok: bool) -> None:
        self._target_files_ok = ok

    def collect(self) -> Iterator[Metric]:
        yield from status_families("amplifi", self.statuses.get())
        yield from self._target_families()
        published = self._published.get()
        if published is None:
            return
        yield from self._router_families(published.snapshot)
        yield from self._mesh_point_families(published.snapshot)
        yield from self._client_families(published)
        yield from self._tracked_families(published)
        yield from self._airplay_families(published.snapshot)

    def _target_families(self) -> Iterator[Metric]:
        info = InfoMetricFamily(
            "monitoring_target", "Friendly name and kind for every ping target.", labels=["ip"]
        )
        for target in self._targets.get() or ():
            info.add_metric([target.ip], {"name": sanitise_label(target.name), "kind": target.kind})
        yield info
        if self._target_files_ok is not None:
            yield GaugeMetricFamily(
                "amplifi_target_files_ok",
                "1 when the ping/AirPlay target files were last written successfully.",
                value=float(self._target_files_ok),
            )

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
            info.add_metric(
                labels,
                {"backhaul_band": sanitise_label(mp.backhaul_band), "platform": sanitise_label(mp.platform)},
            )
            rssi.add_metric(labels, float(mp.rssi_min_dbm))
            uptime.add_metric(labels, float(mp.uptime_seconds))
        yield info
        yield rssi
        yield uptime

    def _client_labels(self, client: WifiClient) -> list[str]:
        tracked = self._tracked.get(client.mac)
        if tracked is not None:
            return [client.mac, sanitise_label(tracked.name), tracked.kind]
        kind: ClientLabelKind = "backhaul" if client.is_backhaul else "other"
        return [client.mac, "", kind]

    def _client_families(self, published: PublishedSnapshot) -> Iterator[Metric]:
        snapshot = published.snapshot
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
        for client in published.clients:
            labels = self._client_labels(client)
            info.add_metric(
                labels,
                {
                    "ap": client.ap_mac,
                    "ap_name": sanitise_label(ap_names.get(client.ap_mac, "")),
                    "band": sanitise_label(client.band),
                    "network": sanitise_label(client.network),
                    "mode": sanitise_label(client.mode or ""),
                },
            )
            signal.add_metric(labels, float(client.signal_quality))
            happiness.add_metric(labels, float(client.happiness_score))
            rx_rate.add_metric(labels, client.rx_bitrate_kbps * _KBPS)
            tx_rate.add_metric(labels, client.tx_bitrate_kbps * _KBPS)
            inactive.add_metric(labels, float(client.inactive_seconds))
            rx_bytes.add_metric(labels, float(published.rx_bytes_total[client.mac]))
            tx_bytes.add_metric(labels, float(published.tx_bytes_total[client.mac]))
        yield info
        yield signal
        yield happiness
        yield rx_rate
        yield tx_rate
        yield inactive
        yield rx_bytes
        yield tx_bytes

    def _tracked_families(self, published: PublishedSnapshot) -> Iterator[Metric]:
        # Present for every tracked client whether or not it is on the WiFi, unlike the
        # amplifi_client_* series, so a rule can say "this HomePod is not associated".
        associated = GaugeMetricFamily(
            "amplifi_tracked_client_associated",
            "1 when the router currently lists this tracked client as a WiFi client.",
            labels=_TRACKED_LABELS,
        )
        present = {client.mac for client in published.clients}
        for tracked in self._tracked.values():
            associated.add_metric(
                [tracked.mac, sanitise_label(tracked.name), tracked.kind], float(tracked.mac in present)
            )
        yield associated

    def _airplay_families(self, snapshot: AmplifiSnapshot) -> Iterator[Metric]:
        advertised = GaugeMetricFamily(
            "amplifi_client_airplay_advertised",
            "1 when the router's Bonjour table lists the service for this HomePod.",
            labels=[*_CLIENT_LABELS, "service"],
        )
        for tracked in self._tracked.values():
            if tracked.kind != "homepod":
                continue
            advertised_services = {short_service(s) for s in snapshot.bonjour.get(tracked.mac, frozenset())}
            for service in AIRPLAY_SERVICES:
                present = service in advertised_services
                advertised.add_metric(
                    [tracked.mac, sanitise_label(tracked.name), tracked.kind, service], float(present)
                )
        yield advertised
