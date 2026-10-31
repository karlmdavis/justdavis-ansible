"""Rendering of the probe target files from the device address inventory, at time of use.

* `ping-targets.yml`: the full ping_exporter config (global `ping:` block plus `targets:`), so that
  ping targets follow address changes. The file is rewritten whenever its rendered content differs, so
  a changed `ping:` setting lands on the first poll after a restart; ping_exporter applies target changes
  live but only reads the `ping:` block at start-up (the role restarts the stack when its environment
  file changes, which covers that).
* `airplay-targets.json`: Prometheus `file_sd` for blackbox_exporter's AirPlay TCP probe, one entry per
  HomePod with a fresh address, labelled with the tracked name (what the AirPlay exporter keys on) and
  the AirPlay name (what the dashboard shows).

A tracked device without a fresh address in the inventory gets no target at all, so its ping, TCP and
unicast series end rather than following an old address (the fail-safe the inventory exists for; see
`common/device_addresses.py`). Both files are rendered deterministically so that unchanged content
produces byte-identical output and no spurious rewrites/reloads happen. The same address can
legitimately appear under two kinds (the router as a static target and as the topology's router); the
target list is de-duplicated by address with the first (static) description winning, so that `on (ip)`
joins against `monitoring_target_info` are unique.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from justdavis_monitoring_exporters.common.device_addresses import AddressInventory
from justdavis_monitoring_exporters.common.settings import ClientKind, TrackedClient

AIRPLAY_PORT = 7000
# File names under the shared directory, read by ping_exporter and Prometheus respectively.
PING_TARGETS_FILE = "ping-targets.yml"
AIRPLAY_TARGETS_FILE = "airplay-targets.json"

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
    # The tracked client's AirPlay name, so dashboards can label its ping and port probes the way the
    # airplay_* series are labelled; blank for static targets, the router, and mesh points.
    airplay_name: str = ""
    # Which inventory source supplied the address; blank for static targets.
    source: str = ""


def target_infos(
    static: Sequence[str],
    inventory: AddressInventory,
    tracked: Sequence[TrackedClient],
    *,
    now: float,
    max_age: float,
) -> tuple[TargetInfo, ...]:
    """Static targets first, then the router and mesh points, then the tracked clients with a fresh
    address, one entry per address (the first description wins)."""
    candidates: list[TargetInfo] = [TargetInfo(ip=ip, name=ip, kind="static") for ip in static]
    for mac, observation in inventory.infrastructure(now=now, max_age=max_age):
        role = inventory.role(mac)
        kind: TargetKind = "router" if role == "router" else "mesh_point"
        candidates.append(
            TargetInfo(
                ip=observation.address,
                name=inventory.name(mac) or observation.address,
                kind=kind,
                source=observation.source,
            )
        )
    for client in sorted(tracked, key=lambda t: t.mac):
        latest = inventory.latest(client.mac, now=now, max_age=max_age)
        if latest is not None:
            candidates.append(
                TargetInfo(
                    ip=latest.address,
                    name=client.name,
                    kind=client.kind,
                    airplay_name=client.airplay_name,
                    source=latest.source,
                )
            )
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


def render_airplay_targets(infos: Sequence[TargetInfo]) -> bytes:
    entries: list[dict[str, object]] = [
        {
            "targets": [f"{info.ip}:{AIRPLAY_PORT}"],
            "labels": {"name": info.name, "kind": info.kind, "airplay_name": info.airplay_name},
        }
        for info in infos
        if info.kind == "homepod"
    ]
    return (json.dumps(entries, indent=2, sort_keys=True) + "\n").encode()
