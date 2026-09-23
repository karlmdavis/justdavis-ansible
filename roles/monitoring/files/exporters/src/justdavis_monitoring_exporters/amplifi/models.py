"""Parsed records for the AmpliFi router's `info-async.php` feed."""

from collections.abc import Mapping
from dataclasses import dataclass

from justdavis_monitoring_exporters.common.settings import Mac

BACKHAUL_NETWORK = "Internal network"


@dataclass(frozen=True, slots=True)
class Router:
    mac: Mac
    name: str
    ip: str
    platform: str
    uptime_seconds: int


@dataclass(frozen=True, slots=True)
class MeshPoint:
    mac: Mac
    name: str
    ip: str
    platform: str
    online: bool
    # The device this mesh point's backhaul connects to (the router, or another mesh point when
    # daisy-chained) and its depth in the mesh (the router is level 1); unknown while offline.
    uplink_mac: Mac | None
    level: int | None
    # The router reports the backhaul link and uptime only while the mesh point is online.
    backhaul_band: str | None
    rssi_min_dbm: int | None
    uptime_seconds: int | None
    # The router's own counters of backhaul connections since it booted, and how long ago the last
    # connect and disconnect were. AmpliFi does not document the two counters' exact meanings; a
    # mesh point that keeps re-joining the mesh moves both.
    connections_to: int
    connections_from: int
    last_connected_age_seconds: int
    last_disconnected_age_seconds: int


@dataclass(frozen=True, slots=True)
class WifiClient:
    """One WiFi association. Backhaul links between mesh points appear here with `network` set to
    `BACKHAUL_NETWORK` and no address/hostname/mode."""

    mac: Mac
    ap_mac: Mac
    band: str
    network: str
    ip: str | None
    hostname: str | None
    mode: str | None
    signal_quality: int
    happiness_score: int
    rx_bitrate_kbps: int
    tx_bitrate_kbps: int
    rx_bytes: int
    tx_bytes: int
    inactive_seconds: int

    @property
    def is_backhaul(self) -> bool:
        return self.network == BACKHAUL_NETWORK


@dataclass(frozen=True, slots=True)
class WanPort:
    link: bool
    link_speed_mbps: int | None
    rx_bitrate_kbps: int | None
    tx_bitrate_kbps: int | None


@dataclass(frozen=True, slots=True)
class AmplifiSnapshot:
    router: Router
    mesh_points: tuple[MeshPoint, ...]
    clients: tuple[WifiClient, ...]
    wan_port: WanPort
    bonjour: Mapping[Mac, frozenset[str]]
