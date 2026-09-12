"""Parsed records for the AmpliFi router's `info-async.php` feed."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Router:
    mac: str
    name: str
    ip: str
    platform: str
    uptime_seconds: int


@dataclass(frozen=True, slots=True)
class MeshPoint:
    mac: str
    name: str
    ip: str
    platform: str
    backhaul_band: str
    rssi_min_dbm: int
    uptime_seconds: int


@dataclass(frozen=True, slots=True)
class WifiClient:
    """One WiFi association. Backhaul links between mesh points appear here with `network` set to
    "Internal network" and no address/hostname/mode."""

    mac: str
    ap_mac: str
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


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """The router's device directory entry (DHCP-derived): applies to wired and wireless devices."""

    mac: str
    display_name: str
    connection: str
    ip: str | None


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
    directory: Mapping[str, DirectoryEntry]
    wan_port: WanPort
    bonjour: Mapping[str, frozenset[str]]
