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
    def __init__(self, statuses: SnapshotHolder[ScrapeStatus]) -> None:
        self.statuses = statuses
        self._snapshots: SnapshotHolder[AirplaySnapshot] = SnapshotHolder()

    def publish(self, snapshot: AirplaySnapshot) -> None:
        self._snapshots.set(snapshot)

    def clear(self) -> None:
        self._snapshots.clear()

    def collect(self) -> Iterator[Metric]:
        yield from status_families("airplay", self.statuses.get())
        snapshot = self._snapshots.get()
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
        unicast = GaugeMetricFamily(
            "airplay_service_unicast_resolved",
            "1 when an mDNS query sent straight to the HomePod's address answered this poll; compare with "
            "airplay_service_resolved (multicast) to tell a missing device from a broken multicast path.",
            labels=_LABELS,
        )
        last_seen = GaugeMetricFamily(
            "airplay_service_last_seen_timestamp_seconds",
            "Unix time the service was last resolved (by multicast or unicast) or announced.",
            labels=_LABELS,
        )
        # The playback gauges are read from the HomePod's own record, so they are absent, not 0, for a
        # poll in which it did not answer or its record could not be read: neither must read as
        # "stopped playing". The readable gauge tells the two apart.
        readable = GaugeMetricFamily(
            "airplay_playback_state_readable",
            "1 when the HomePod answered this poll and its AirPlay record had the form the playback "
            "gauges are read from, 0 when it answered with a record that did not (the exporter's log "
            "says how); absent when it did not answer.",
            labels=["name"],
        )
        audio_playing = GaugeMetricFamily(
            "airplay_audio_playing",
            "1 while the HomePod reports that audio is playing, whatever the source; 0 when it reports "
            "that none is.",
            labels=["name"],
        )
        group_leader = GaugeMetricFamily(
            "airplay_group_leader",
            "1 when the HomePod leads its group (one on its own leads a group of one), 0 when it follows "
            "another device: a HomePod, or a phone, tablet, or computer sending AirPlay.",
            labels=["name"],
        )
        group_info = GaugeMetricFamily(
            "airplay_group_info",
            "Always 1; the group label is the id of the group or session the HomePod is in, shared by "
            "the HomePods playing together.",
            labels=["name", "group"],
        )
        status_flags = GaugeMetricFamily(
            "airplay_status_flags",
            "The flags field of the HomePod's AirPlay record, as a number; only bit 20 (audio playing) is "
            "interpreted, the rest is kept for later comparison.",
            labels=["name"],
        )
        for key, seen in snapshot.services.items():
            labels = [sanitise_label(key.name), key.service]
            resolved.add_metric(labels, float(seen.resolved))
            discovered.add_metric(labels, float(seen.discovered))
            if seen.unicast_resolved is not None:
                unicast.add_metric(labels, float(seen.unicast_resolved))
            if seen.last_seen is not None:
                last_seen.add_metric(labels, seen.last_seen)
            name = sanitise_label(key.name)
            if seen.state is not None or seen.state_unreadable is not None:
                readable.add_metric([name], float(seen.state is not None))
            if seen.state is not None:
                audio_playing.add_metric([name], float(seen.state.audio_playing))
                group_leader.add_metric([name], float(seen.state.group_leader))
                group_info.add_metric([name, seen.state.group_id], 1.0)
                status_flags.add_metric([name], float(seen.state.status_flags))
        yield resolved
        yield discovered
        yield unicast
        yield last_seen
        yield readable
        yield audio_playing
        yield group_leader
        yield group_info
        yield status_flags
