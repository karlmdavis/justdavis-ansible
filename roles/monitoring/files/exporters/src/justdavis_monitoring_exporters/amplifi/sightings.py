"""The router feed as a producer for the device address inventory (`common/device_addresses.py`).

What counts as a sighting: the router itself, every online mesh point, every WiFi association that
carries an address, and every device on an Ethernet port whose directory entry carries an address. A
directory entry on its own does not: it is a lease the router remembers, not evidence that the device
holds the address now. Each client sighting carries the DHCP hostname, which is what lets the inventory
attribute mDNS sightings (hostname only) to a MAC. Addresses are validated by the store; what each entry
knows beyond the core (wired or wireless, access point, band, lease) travels in `extra`, unread yet.
"""

from justdavis_monitoring_exporters.amplifi.collector import dedupe_clients
from justdavis_monitoring_exporters.amplifi.models import AmplifiSnapshot
from justdavis_monitoring_exporters.common.device_addresses import Sighting


def router_sightings(snapshot: AmplifiSnapshot, *, now: float) -> tuple[Sighting, ...]:
    sightings: list[Sighting] = [
        Sighting(
            address=snapshot.router.ip,
            observed_at=now,
            mac=snapshot.router.mac,
            name=snapshot.router.name,
            role="router",
            extra={"platform": snapshot.router.platform},
        )
    ]
    for mesh_point in sorted(snapshot.mesh_points, key=lambda mp: mp.mac):
        if mesh_point.online:
            sightings.append(
                Sighting(
                    address=mesh_point.ip,
                    observed_at=now,
                    mac=mesh_point.mac,
                    name=mesh_point.name,
                    role="mesh_point",
                    extra={"platform": mesh_point.platform},
                )
            )
    for client in sorted(dedupe_clients(snapshot.clients), key=lambda c: c.mac):
        if client.ip is None or client.is_backhaul:
            continue
        sightings.append(
            Sighting(
                address=client.ip,
                observed_at=now,
                mac=client.mac,
                hostname=client.hostname or "",
                name=client.hostname or "",
                extra={"connection": "wireless", "ap": client.ap_mac, "band": client.band},
            )
        )
    for mac in sorted(snapshot.wired):
        entry = snapshot.directory.get(mac)
        if entry is None or entry.ip is None:
            continue
        extra = {"connection": "ethernet"}
        if entry.lease_validity is not None:
            extra["lease_validity"] = str(entry.lease_validity)
        sightings.append(
            Sighting(
                address=entry.ip,
                observed_at=now,
                mac=mac,
                hostname=entry.hostname or "",
                name=entry.hostname or "",
                extra=extra,
            )
        )
    return tuple(sightings)
