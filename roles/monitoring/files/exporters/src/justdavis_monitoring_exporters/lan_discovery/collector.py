"""Prometheus collector for the LAN discovery exporter: the rendered targets and address freshness."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily, Metric
from prometheus_client.registry import Collector

from justdavis_monitoring_exporters.common.device_addresses import SOURCES, Source
from justdavis_monitoring_exporters.common.labels import sanitise_label
from justdavis_monitoring_exporters.common.metrics import status_families
from justdavis_monitoring_exporters.common.settings import TrackedClient
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder
from justdavis_monitoring_exporters.lan_discovery.targets import TargetInfo

_TRACKED_LABELS = ["mac", "name", "kind", "airplay_name", "expect_online"]


@dataclass(frozen=True, slots=True)
class TrackedAddressState:
    """What the inventory knows about one tracked client at the time of the poll."""

    client: TrackedClient
    age_seconds: float | None
    fresh: bool
    source: str


@dataclass(frozen=True, slots=True)
class LanDiscoverySnapshot:
    targets: tuple[TargetInfo, ...]
    tracked: tuple[TrackedAddressState, ...]
    source_written_at: Mapping[Source, float | None]
    # The sources this exporter itself runs (mdns, arp); the router source is the AmpliFi exporter's.
    enabled_sources: frozenset[str]


class LanDiscoveryCollector(Collector):
    def __init__(self, statuses: SnapshotHolder[ScrapeStatus]) -> None:
        self.statuses = statuses
        self._published: SnapshotHolder[LanDiscoverySnapshot] = SnapshotHolder()
        self._target_files_ok: bool | None = None

    def publish(self, snapshot: LanDiscoverySnapshot) -> None:
        self._published.set(snapshot)

    def clear(self) -> None:
        self._published.clear()

    def set_target_files_ok(self, ok: bool) -> None:
        self._target_files_ok = ok

    def collect(self) -> Iterator[Metric]:
        yield from status_families("lan_discovery", self.statuses.get())
        if self._target_files_ok is not None:
            yield GaugeMetricFamily(
                "lan_discovery_target_files_ok",
                "1 when the ping and AirPlay probe target files were last rendered and written "
                "successfully (from the last poll).",
                value=float(self._target_files_ok),
            )
        published = self._published.get()
        if published is None:
            return
        yield from _target_families(published.targets)
        yield from _tracked_families(published.tracked)
        yield from _source_families(published.source_written_at, published.enabled_sources)


def _target_families(targets: Sequence[TargetInfo]) -> Iterator[Metric]:
    info = InfoMetricFamily(
        "monitoring_target", "Friendly name and kind for every ping target.", labels=["ip"]
    )
    for target in targets:
        info.add_metric(
            [target.ip],
            {
                "name": sanitise_label(target.name),
                "kind": target.kind,
                "airplay_name": sanitise_label(target.airplay_name),
            },
        )
    yield info


def _tracked_families(states: Sequence[TrackedAddressState]) -> Iterator[Metric]:
    fresh = GaugeMetricFamily(
        "device_address_fresh",
        "1 when some source has seen the tracked device's address within the freshness window, so it "
        "is being probed; 0 when it has no usable address and its probe series are absent (from the "
        "last poll). expect_online is the tracked-client flag: 1 for a device that should always be "
        "on the LAN, which is what TrackedDeviceAddressUnknown alerts on.",
        labels=_TRACKED_LABELS,
    )
    age = GaugeMetricFamily(
        "device_address_age_seconds",
        "Seconds since any source last saw the tracked device's address, at the last poll; absent for a "
        "device no source has ever seen. The source label names the one supplying the address in use, "
        "or the freshest when none is fresh.",
        labels=[*_TRACKED_LABELS, "source"],
    )
    for state in states:
        labels = [
            state.client.mac,
            sanitise_label(state.client.name),
            state.client.kind,
            sanitise_label(state.client.airplay_name),
            "1" if state.client.expect_online else "0",
        ]
        fresh.add_metric(labels, float(state.fresh))
        if state.age_seconds is not None:
            age.add_metric([*labels, state.source], state.age_seconds)
    yield fresh
    yield age


def _source_families(written_at: Mapping[Source, float | None], enabled: frozenset[str]) -> Iterator[Metric]:
    last_write = GaugeMetricFamily(
        "device_address_source_last_write_timestamp_seconds",
        "Unix time the source's inventory file was last written by its producer (0 when the file is "
        "absent), read at the last poll.",
        labels=["source"],
    )
    for source in SOURCES:
        last_write.add_metric([source], written_at.get(source) or 0.0)
    yield last_write
    running = GaugeMetricFamily(
        "device_address_source_enabled",
        "1 when this exporter runs the source (mdns, arp); 0 when it is switched off by configuration "
        "(no LAN address or subnet), so a file it never writes is not mistaken for a stale one.",
        labels=["source"],
    )
    for source in ("mdns", "arp"):
        running.add_metric([source], float(source in enabled))
    yield running
