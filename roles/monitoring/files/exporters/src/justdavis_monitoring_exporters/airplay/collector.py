"""Prometheus collector for the AirPlay mDNS probe snapshot."""

from collections.abc import Iterator

from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from justdavis_monitoring_exporters.airplay.models import AirplaySnapshot
from justdavis_monitoring_exporters.common.labels import sanitise_label
from justdavis_monitoring_exporters.common.metrics import status_families
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder

_LABELS = ["name", "service"]


class AirplayCollector(Collector):
    def __init__(
        self, snapshots: SnapshotHolder[AirplaySnapshot], statuses: SnapshotHolder[ScrapeStatus]
    ) -> None:
        self.snapshots = snapshots
        self.statuses = statuses

    def collect(self) -> Iterator[Metric]:
        yield from status_families("airplay", self.statuses.get())
        snapshot = self.snapshots.get()
        if snapshot is None:
            return
        resolved = GaugeMetricFamily(
            "airplay_service_resolved",
            "1 when an active mDNS query for the service succeeded this poll.",
            labels=_LABELS,
        )
        discovered = GaugeMetricFamily(
            "airplay_service_discovered",
            "1 when the passive mDNS browser currently lists the service.",
            labels=_LABELS,
        )
        last_seen = GaugeMetricFamily(
            "airplay_service_last_seen_timestamp_seconds",
            "Unix time the service was last resolved or announced.",
            labels=_LABELS,
        )
        for key, ok in snapshot.resolved.items():
            labels = [sanitise_label(key.name), key.service]
            resolved.add_metric(labels, float(ok))
            discovered.add_metric(labels, float(key in snapshot.discovered))
            seen = snapshot.last_seen.get(key)
            if seen is not None:
                last_seen.add_metric(labels, seen)
        yield resolved
        yield discovered
        yield last_seen
