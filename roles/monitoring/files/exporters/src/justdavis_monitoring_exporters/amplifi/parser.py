"""Parser for the AmpliFi router's `POST /info-async.php` (`do=full`) JSON feed.

Verified against an AmpliFi HD router (AFi-R-HD, protocol 140) with two AFi-P-HD mesh points in
September 2026. The response is a JSON array of six elements:

0. Topology: `{router_mac: {friendly_name, ip, mac, platform_name, role, uptime, children: {wifi:
   {mesh_point_mac: {friendly_name, ip, mac, platform_name, active_band, overriden_active_band,
   rssi_min, uptime, ...}}}}}`.
1. WiFi associations: `{ap_mac: {band: {network_type: {client_mac: {Address?, HostName?, Mode?,
   SignalQuality, HappinessScore, RxBitrate, TxBitrate (kbit/s), RxBytes, TxBytes (32-bit counters),
   Inactive, ...}}}}}`. Band is "2.4 GHz" or "5 GHz"; network type is "User network", "Guest network"
   or "Internal network" (mesh backhaul, which lacks Address/HostName/Mode). The same client MAC can
   appear under more than one access point while the router still holds a stale association.
2. Device directory: `{mac: {host_name?, description?, ip?, connection, icon_id, ...}}` (not used).
3. Wired port map: `{router_mac: {client_mac: port}}` (not used).
4. Ethernet ports: `{router_mac: {"eth-0": {link, link_speed?, rx_bitrate?, tx_bitrate?}, ...}}`; eth-0
   is the WAN port.
5. Bonjour/device fingerprints: `{mac: {bonjour?: {ip, services: [...]}, device: {...}}}`.

MAC addresses are canonicalised to lowercase colon form so they join with the tracked-client
configuration. This module is pure: stdlib only, no I/O, and it never returns partial results.
"""

import json
from collections.abc import Mapping

from justdavis_monitoring_exporters.amplifi.models import (
    AmplifiSnapshot,
    MeshPoint,
    Router,
    WanPort,
    WifiClient,
)
from justdavis_monitoring_exporters.common.errors import ParseError
from justdavis_monitoring_exporters.common.jsonutil import as_bool, as_dict, as_int, as_list, as_str, get
from justdavis_monitoring_exporters.common.settings import canonical_mac

_EXPECTED_ELEMENTS = 6
_WAN_PORT = "eth-0"


def _optional_str(mapping: Mapping[str, object], key: str, ctx: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    return as_str(value, f"{ctx}.{key}")


def _optional_int(mapping: Mapping[str, object], key: str, ctx: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    return as_int(value, f"{ctx}.{key}")


def _mesh_point_name(friendly_name: str) -> str:
    """AmpliFi appends model and MAC suffix to unnamed mesh points: "Living Room (AFi-P-HD-000003)"."""
    return friendly_name.split(" (")[0]


def _parse_topology(element: object) -> tuple[Router, tuple[MeshPoint, ...]]:
    topology = as_dict(element, "[0]")
    if len(topology) != 1:
        raise ParseError(f"[0]: expected exactly one router, got {len(topology)}")
    router_mac, router_value = next(iter(topology.items()))
    ctx = f"[0].{router_mac}"
    router_map = as_dict(router_value, ctx)
    router = Router(
        mac=canonical_mac(as_str(get(router_map, "mac", ctx), f"{ctx}.mac")),
        name=as_str(get(router_map, "friendly_name", ctx), f"{ctx}.friendly_name"),
        ip=as_str(get(router_map, "ip", ctx), f"{ctx}.ip"),
        platform=as_str(get(router_map, "platform_name", ctx), f"{ctx}.platform_name"),
        uptime_seconds=as_int(get(router_map, "uptime", ctx), f"{ctx}.uptime"),
    )
    children = as_dict(router_map.get("children", {}), f"{ctx}.children")
    wifi_children = as_dict(children.get("wifi", {}), f"{ctx}.children.wifi")
    mesh_points: list[MeshPoint] = []
    for mp_mac, mp_value in wifi_children.items():
        mp_ctx = f"{ctx}.children.wifi.{mp_mac}"
        mp = as_dict(mp_value, mp_ctx)
        mesh_points.append(
            MeshPoint(
                mac=canonical_mac(as_str(get(mp, "mac", mp_ctx), f"{mp_ctx}.mac")),
                name=_mesh_point_name(as_str(get(mp, "friendly_name", mp_ctx), f"{mp_ctx}.friendly_name")),
                ip=as_str(get(mp, "ip", mp_ctx), f"{mp_ctx}.ip"),
                platform=as_str(get(mp, "platform_name", mp_ctx), f"{mp_ctx}.platform_name"),
                backhaul_band=as_str(get(mp, "active_band", mp_ctx), f"{mp_ctx}.active_band"),
                rssi_min_dbm=as_int(get(mp, "rssi_min", mp_ctx), f"{mp_ctx}.rssi_min"),
                uptime_seconds=as_int(get(mp, "uptime", mp_ctx), f"{mp_ctx}.uptime"),
            )
        )
    return router, tuple(mesh_points)


def _parse_clients(element: object) -> tuple[WifiClient, ...]:
    clients: list[WifiClient] = []
    for ap_mac, bands_value in as_dict(element, "[1]").items():
        for band, networks_value in as_dict(bands_value, f"[1].{ap_mac}").items():
            for network, macs_value in as_dict(networks_value, f"[1].{ap_mac}.{band}").items():
                for client_mac, client_value in as_dict(macs_value, f"[1].{ap_mac}.{band}.{network}").items():
                    ctx = f"[1].{ap_mac}.{band}.{network}.{client_mac}"
                    client = as_dict(client_value, ctx)
                    clients.append(
                        WifiClient(
                            mac=canonical_mac(client_mac),
                            ap_mac=canonical_mac(ap_mac),
                            band=band,
                            network=network,
                            ip=_optional_str(client, "Address", ctx),
                            hostname=_optional_str(client, "HostName", ctx),
                            mode=_optional_str(client, "Mode", ctx),
                            signal_quality=as_int(get(client, "SignalQuality", ctx), f"{ctx}.SignalQuality"),
                            happiness_score=as_int(
                                get(client, "HappinessScore", ctx), f"{ctx}.HappinessScore"
                            ),
                            rx_bitrate_kbps=as_int(get(client, "RxBitrate", ctx), f"{ctx}.RxBitrate"),
                            tx_bitrate_kbps=as_int(get(client, "TxBitrate", ctx), f"{ctx}.TxBitrate"),
                            rx_bytes=as_int(get(client, "RxBytes", ctx), f"{ctx}.RxBytes"),
                            tx_bytes=as_int(get(client, "TxBytes", ctx), f"{ctx}.TxBytes"),
                            inactive_seconds=as_int(get(client, "Inactive", ctx), f"{ctx}.Inactive"),
                        )
                    )
    return tuple(clients)


def _parse_wan_port(element: object, router_mac: str) -> WanPort:
    ports_by_router = as_dict(element, "[4]")
    ports: Mapping[str, object] | None = None
    for candidate_mac, candidate_value in ports_by_router.items():
        if canonical_mac(candidate_mac) == router_mac:
            ports = as_dict(candidate_value, f"[4].{candidate_mac}")
            break
    if ports is None:
        raise ParseError(f"[4].{router_mac}: router ports not found")
    ctx = f"[4].{router_mac}.{_WAN_PORT}"
    wan = as_dict(get(ports, _WAN_PORT, f"[4].{router_mac}"), ctx)
    return WanPort(
        link=as_bool(get(wan, "link", ctx), f"{ctx}.link"),
        link_speed_mbps=_optional_int(wan, "link_speed", ctx),
        rx_bitrate_kbps=_optional_int(wan, "rx_bitrate", ctx),
        tx_bitrate_kbps=_optional_int(wan, "tx_bitrate", ctx),
    )


def _parse_bonjour(element: object) -> dict[str, frozenset[str]]:
    """Return, per MAC, the set of advertised service types (e.g. "_airplay._tcp.local")."""
    bonjour: dict[str, frozenset[str]] = {}
    for mac, entry_value in as_dict(element, "[5]").items():
        ctx = f"[5].{mac}"
        entry = as_dict(entry_value, ctx)
        bonjour_value = entry.get("bonjour")
        if bonjour_value is None:
            continue
        services = as_list(
            get(as_dict(bonjour_value, f"{ctx}.bonjour"), "services", f"{ctx}.bonjour"),
            f"{ctx}.bonjour.services",
        )
        types = {as_str(service, f"{ctx}.bonjour.services[]") for service in services}
        bonjour[canonical_mac(mac)] = frozenset(service for service in types if service.startswith("_"))
    return bonjour


def parse_info_async(body: bytes | str) -> AmplifiSnapshot:
    """Parse the `info-async.php` response body into an `AmplifiSnapshot`."""
    try:
        decoded: object = json.loads(body)
    except ValueError as exc:
        raise ParseError("body: not valid JSON") from exc
    elements = as_list(decoded, "body")
    if len(elements) != _EXPECTED_ELEMENTS:
        raise ParseError(f"body: expected {_EXPECTED_ELEMENTS} elements, got {len(elements)}")
    router, mesh_points = _parse_topology(elements[0])
    return AmplifiSnapshot(
        router=router,
        mesh_points=mesh_points,
        clients=_parse_clients(elements[1]),
        wan_port=_parse_wan_port(elements[4], router.mac),
        bonjour=_parse_bonjour(elements[5]),
    )
