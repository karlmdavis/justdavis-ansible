"""Rendering of the dynamic target files driven by the AmpliFi snapshot.

* `ping-targets.yml`: the full ping_exporter config (global `ping:` block plus `targets:`), so that
  ping targets follow DHCP address changes. ping_exporter watches the file and applies target changes
  live; changes to the `ping:` block only take effect when ping_exporter restarts (the role restarts
  the stack when its environment file changes).
* `airplay-targets.json`: Prometheus `file_sd` for blackbox_exporter's AirPlay TCP probe, one entry per
  HomePod currently associated with the WiFi.

Both are rendered deterministically so that unchanged content produces byte-identical output and no
spurious rewrites/reloads happen. The same IP can legitimately appear under two kinds (for example the
router as a static target and as the topology's router); ping targets are de-duplicated by IP, while
`monitoring_target_info` keeps both descriptions.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from justdavis_monitoring_exporters.amplifi.models import AmplifiSnapshot
from justdavis_monitoring_exporters.common.settings import ClientKind, TrackedClient

AIRPLAY_PORT = 7000

type TargetKind = Literal["static", "router", "mesh_point"] | ClientKind


@dataclass(frozen=True, slots=True)
class PingConfig:
    interval_seconds: int
    timeout_seconds: int
    history_size: int


@dataclass(frozen=True, slots=True)
class TargetInfo:
    ip: str
    name: str
    kind: TargetKind


def target_infos(
    static: Sequence[str], snapshot: AmplifiSnapshot | None, tracked: Sequence[TrackedClient]
) -> tuple[TargetInfo, ...]:
    """Static targets first, then the router, mesh points, and tracked clients present in the snapshot."""
    infos: list[TargetInfo] = [TargetInfo(ip=ip, name=ip, kind="static") for ip in static]
    if snapshot is None:
        return tuple(infos)
    infos.append(TargetInfo(ip=snapshot.router.ip, name=snapshot.router.name, kind="router"))
    for mesh_point in sorted(snapshot.mesh_points, key=lambda mp: mp.mac):
        infos.append(TargetInfo(ip=mesh_point.ip, name=mesh_point.name, kind="mesh_point"))
    ip_by_mac = {client.mac: client.ip for client in snapshot.clients if client.ip is not None}
    for tracked_client in sorted(tracked, key=lambda t: t.mac):
        ip = ip_by_mac.get(tracked_client.mac)
        if ip is not None:
            infos.append(TargetInfo(ip=ip, name=tracked_client.name, kind=tracked_client.kind))
    return tuple(infos)


def render_ping_targets(ping: PingConfig, infos: Sequence[TargetInfo]) -> bytes:
    lines = [
        "ping:",
        f"  interval: {ping.interval_seconds}s",
        f"  timeout: {ping.timeout_seconds}s",
        f"  history-size: {ping.history_size}",
        "targets:",
    ]
    seen: set[str] = set()
    for info in infos:
        if info.ip not in seen:
            seen.add(info.ip)
            lines.append(f"  - {info.ip}")
    return ("\n".join(lines) + "\n").encode()


def render_airplay_targets(snapshot: AmplifiSnapshot | None, tracked: Sequence[TrackedClient]) -> bytes:
    entries: list[dict[str, object]] = []
    if snapshot is not None:
        ip_by_mac = {client.mac: client.ip for client in snapshot.clients if client.ip is not None}
        for tracked_client in sorted(tracked, key=lambda t: t.mac):
            ip = ip_by_mac.get(tracked_client.mac)
            if tracked_client.kind == "homepod" and ip is not None:
                entries.append(
                    {
                        "targets": [f"{ip}:{AIRPLAY_PORT}"],
                        "labels": {"name": tracked_client.name, "kind": tracked_client.kind},
                    }
                )
    return (json.dumps(entries, indent=2, sort_keys=True) + "\n").encode()
