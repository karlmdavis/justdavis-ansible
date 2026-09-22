"""Rendering of the dynamic target files driven by the AmpliFi snapshot.

* `ping-targets.yml`: the full ping_exporter config (global `ping:` block plus `targets:`), so that
  ping targets follow DHCP address changes. The file is rewritten whenever its rendered content
  differs, so a changed `ping:` setting lands on the first successful poll after a restart;
  ping_exporter applies target changes live but only reads the `ping:` block at start-up (the role
  restarts the stack when its environment file changes, which covers that).
* `airplay-targets.json`: Prometheus `file_sd` for blackbox_exporter's AirPlay TCP probe, one entry per
  HomePod currently associated with the WiFi.

Both are rendered deterministically so that unchanged content produces byte-identical output and no
spurious rewrites/reloads happen. The same IP can legitimately appear under two kinds (for example the
router as a static target and as the topology's router); the target list is de-duplicated by IP with
the first (static) description winning, so that `on (ip)` joins against `monitoring_target_info` are
unique. Addresses the router reports are validated, because an empty or malformed one would break
ping_exporter's config reload.
"""

import ipaddress
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from justdavis_monitoring_exporters.amplifi.models import AmplifiSnapshot
from justdavis_monitoring_exporters.common.settings import ClientKind, TrackedClient

log = logging.getLogger(__name__)

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


def _valid_ip(ip: str | None, what: str) -> str | None:
    """The address as the router gave it, or None (with a warning) when it is not a usable address."""
    if ip is None:
        return None
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        log.warning("ignoring %s: %r is not an IP address", what, ip)
        return None
    return ip


def target_infos(
    static: Sequence[str], snapshot: AmplifiSnapshot | None, tracked: Sequence[TrackedClient]
) -> tuple[TargetInfo, ...]:
    """Static targets first, then the router, mesh points, and tracked clients present in the snapshot,
    with one entry per address (the first description wins)."""
    candidates: list[TargetInfo] = [TargetInfo(ip=ip, name=ip, kind="static") for ip in static]
    if snapshot is not None:
        router = snapshot.router
        if _valid_ip(router.ip, f"router {router.mac} address") is not None:
            candidates.append(TargetInfo(ip=router.ip, name=router.name, kind="router"))
        for mesh_point in sorted(snapshot.mesh_points, key=lambda mp: mp.mac):
            if _valid_ip(mesh_point.ip, f"mesh point {mesh_point.mac} address") is not None:
                candidates.append(TargetInfo(ip=mesh_point.ip, name=mesh_point.name, kind="mesh_point"))
        ip_by_mac = {client.mac: client.ip for client in snapshot.clients if client.ip is not None}
        for tracked_client in sorted(tracked, key=lambda t: t.mac):
            ip = _valid_ip(ip_by_mac.get(tracked_client.mac), f"client {tracked_client.mac} address")
            if ip is not None:
                candidates.append(TargetInfo(ip=ip, name=tracked_client.name, kind=tracked_client.kind))
    infos: list[TargetInfo] = []
    seen: set[str] = set()
    for info in candidates:
        if info.ip not in seen:
            seen.add(info.ip)
            infos.append(info)
    return tuple(infos)


def render_ping_targets(ping: PingConfig, infos: Sequence[TargetInfo]) -> bytes:
    lines = [
        "ping:",
        f"  interval: {ping.interval_seconds}s",
        f"  timeout: {ping.timeout_seconds}s",
        f"  history-size: {ping.history_size}",
        "targets:",
    ]
    lines.extend(f"  - {info.ip}" for info in infos)
    return ("\n".join(lines) + "\n").encode()


def render_airplay_targets(snapshot: AmplifiSnapshot | None, tracked: Sequence[TrackedClient]) -> bytes:
    entries: list[dict[str, object]] = []
    if snapshot is not None:
        ip_by_mac = {client.mac: client.ip for client in snapshot.clients if client.ip is not None}
        for tracked_client in sorted(tracked, key=lambda t: t.mac):
            ip = _valid_ip(ip_by_mac.get(tracked_client.mac), f"client {tracked_client.mac} address")
            if tracked_client.kind == "homepod" and ip is not None:
                entries.append(
                    {
                        "targets": [f"{ip}:{AIRPLAY_PORT}"],
                        "labels": {"name": tracked_client.name, "kind": tracked_client.kind},
                    }
                )
    return (json.dumps(entries, indent=2, sort_keys=True) + "\n").encode()
