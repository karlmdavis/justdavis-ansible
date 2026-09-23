"""Parser for the AmpliFi router's `POST /info-async.php` (`do=full`) JSON feed.

Verified against an AmpliFi HD router (AFi-R-HD, protocol 140) with two AFi-P-HD mesh points in
September 2026. The response is a JSON array of six elements:

0. Topology: `{router_mac: {friendly_name, ip, mac, platform_name, role, uptime, children: {wifi:
   {mesh_point_mac: {friendly_name, ip, mac, platform_name, active_band, overriden_active_band,
   rssi_min, uptime, connections_to, connections_from, last_connected, last_disconnected, ...}},
   offline: {mesh_point_mac: {...}}}}}`. A mesh point whose backhaul goes through another mesh
   point (daisy-chained) is nested under that mesh point's own `children.wifi`, with `master_peer`
   naming its uplink and `level` its depth (the router is 1), so the tree is walked recursively.
   A mesh point the router has lost moves from `wifi` to
   `offline`, keeping its identity, address, and connection counters but losing `active_band`,
   `rssi_min`, and `uptime`; those three can also be missing from a `wifi` entry for one poll while
   the mesh point is on its way out. The `last_*` fields are ages in seconds, not timestamps.
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
from justdavis_monitoring_exporters.common.settings import Mac, canonical_mac

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
    """AmpliFi appends the model and a MAC suffix to some mesh points' names, as in
    "Living Room (AFi-P-HD-000003)"; keep the part before it (which also truncates a user-chosen
    name containing " (")."""
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
    return router, tuple(_parse_mesh_points(router_map, ctx))


def _parse_mesh_points(node: Mapping[str, object], ctx: str) -> list[MeshPoint]:
    """The mesh points under `node` (the router, or a mesh point with daisy-chained children), and
    recursively theirs."""
    children = as_dict(node.get("children", {}), f"{ctx}.children")
    mesh_points: list[MeshPoint] = []
    for group, online in (("wifi", True), ("offline", False)):
        for mp_mac, mp_value in as_dict(children.get(group, {}), f"{ctx}.children.{group}").items():
            mp_ctx = f"{ctx}.children.{group}.{mp_mac}"
            mp = as_dict(mp_value, mp_ctx)
            uplink = _optional_str(mp, "master_peer", mp_ctx) if online else None
            mesh_points.append(
                MeshPoint(
                    mac=canonical_mac(as_str(get(mp, "mac", mp_ctx), f"{mp_ctx}.mac")),
                    name=_mesh_point_name(
                        as_str(get(mp, "friendly_name", mp_ctx), f"{mp_ctx}.friendly_name")
                    ),
                    ip=as_str(get(mp, "ip", mp_ctx), f"{mp_ctx}.ip"),
                    platform=as_str(get(mp, "platform_name", mp_ctx), f"{mp_ctx}.platform_name"),
                    online=online,
                    uplink_mac=None if uplink is None else canonical_mac(uplink),
                    level=_optional_int(mp, "level", mp_ctx) if online else None,
                    backhaul_band=_optional_str(mp, "active_band", mp_ctx) if online else None,
                    rssi_min_dbm=_optional_int(mp, "rssi_min", mp_ctx) if online else None,
                    uptime_seconds=_optional_int(mp, "uptime", mp_ctx) if online else None,
                    connections_to=as_int(get(mp, "connections_to", mp_ctx), f"{mp_ctx}.connections_to"),
                    connections_from=as_int(
                        get(mp, "connections_from", mp_ctx), f"{mp_ctx}.connections_from"
                    ),
                    last_connected_age_seconds=as_int(
                        get(mp, "last_connected", mp_ctx), f"{mp_ctx}.last_connected"
                    ),
                    last_disconnected_age_seconds=as_int(
                        get(mp, "last_disconnected", mp_ctx), f"{mp_ctx}.last_disconnected"
                    ),
                )
            )
            mesh_points.extend(_parse_mesh_points(mp, mp_ctx))
    return mesh_points


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


def _parse_wan_port(element: object, router_mac: Mac) -> WanPort:
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


def _parse_bonjour(element: object) -> dict[Mac, frozenset[str]]:
    """Return, per MAC, the set of advertised service types (e.g. "_airplay._tcp.local")."""
    bonjour: dict[Mac, frozenset[str]] = {}
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
