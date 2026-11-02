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
    # mesh point that keeps re-joining the mesh moves both. The two ages are absent until the event
    # has happened since the router booted (seen 2026-10-30: a mesh point connected once since the
    # reboot and never dropped carried no `last_disconnected`).
    connections_to: int
    connections_from: int
    last_connected_age_seconds: int | None
    last_disconnected_age_seconds: int | None


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
    # Negotiated PHY link rates (RxBitrate/TxBitrate), not throughput.
    rx_bitrate_kbps: int
    tx_bitrate_kbps: int
    # Per-association 32-bit counters; rx is what the client received. See common/counters.py.
    rx_bytes: int
    tx_bytes: int
    inactive_seconds: int

    @property
    def is_backhaul(self) -> bool:
        return self.network == BACKHAUL_NETWORK


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """One device in the router's directory (element 2): every device it holds a DHCP lease or a wired
    port for, wireless or not, so this is where a wired device's address comes from."""

    mac: Mac
    ip: str | None
    hostname: str | None
    # "wireless" or "ethernet", as the router spells it.
    connection: str | None
    # Seconds of DHCP lease left; -1 for a lease the router treats as permanent (2026-10-31: the
    # wired devices and the devices with a reserved address).
    lease_validity: int | None


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
    directory: Mapping[Mac, DirectoryEntry]
    # The clients the router (or a mesh point) lists on its own Ethernet ports (element 3).
    wired: frozenset[Mac]
