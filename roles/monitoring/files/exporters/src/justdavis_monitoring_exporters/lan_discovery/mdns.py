"""mDNS announcements as a producer for the device address inventory.

The LAN's Apple devices announce `_airplay._tcp` and `_raop._tcp`, the Hue bridge `_hue._tcp`; each
instance's SRV record names a host whose A record is the address. A `ServiceBrowser` keeps those records
flowing into zeroconf's cache; this module reads the cache each poll and turns every (device, address)
it finds into a sighting timed by the A record's arrival. The device is identified by its MAC where
the records carry one (`_hue._tcp` TXT `bridgeid` is the MAC with `fffe` inserted in the middle:
`020000fffe000030` is `02:00:00:00:00:30`) and otherwise by the SRV hostname, which is the DHCP
hostname the router's directory pairs with the MAC: the AirPlay TXT `deviceid` and the `_raop` instance
prefix look like MACs but are a separate identifier (2026-10-31: none of the nine HomePods' matched its
WiFi MAC), so they travel in `extra` and identify nothing.

What the timing means (2026-10-31): devices announce at start and on change, and a passive browser
re-queries only PTR records near their expiry, so a quiet device's A record ages in the cache until
something asks for it; this source is therefore opportunistic, and the neighbour table is the one that
carries the day for a device nothing has asked about. zeroconf's record timestamps are monotonic
milliseconds, converted to epoch seconds here with both clocks read at the same moment.
"""

import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from zeroconf import IPVersion, ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

# zeroconf does not re-export its wire-level classes, its cache, or its record-type constants.
from zeroconf._cache import DNSCache
from zeroconf._dns import DNSAddress, DNSPointer, DNSService, DNSText
from zeroconf.const import _CLASS_IN, _TYPE_A, _TYPE_PTR

from justdavis_monitoring_exporters.airplay.state import parse_txt
from justdavis_monitoring_exporters.common.device_addresses import Sighting, canonical_hostname
from justdavis_monitoring_exporters.common.mdns import service_type, short_service
from justdavis_monitoring_exporters.common.settings import Mac, normalise_mac

log = logging.getLogger(__name__)

# The services whose records identify the device by MAC.
MDNS_SERVICES: tuple[str, ...] = ("_airplay._tcp", "_raop._tcp", "_hue._tcp")


@dataclass(frozen=True, slots=True)
class CachedInstance:
    """One service instance as the cache holds it: short service type, instance name (without the
    type), the host its SRV record names, its TXT fields, and the host's IPv4 addresses with the epoch
    time each A record was last received."""

    service: str
    instance: str
    fqdn: str
    server: str | None
    txt: Mapping[str, str]
    addresses: tuple[tuple[str, float], ...]


def mac_from_instance(service: str, txt: Mapping[str, str]) -> Mac | None:
    """The MAC the instance's records identify the device by, or None when they carry none."""
    if service == "_hue._tcp":
        bridge_id = txt.get("bridgeid", "").lower()
        if len(bridge_id) == 16 and bridge_id[6:10] == "fffe":
            return _mac_from_hex(bridge_id[:6] + bridge_id[10:])
    return None


def device_identifier(service: str, instance: str, txt: Mapping[str, str]) -> str:
    """The identifier the instance advertises (AirPlay `deviceid`, the RAOP prefix, the Hue bridge id),
    kept in `extra` for the record."""
    if service == "_airplay._tcp":
        return txt.get("deviceid", "")
    if service == "_raop._tcp":
        prefix, separator, _name = instance.partition("@")
        return prefix if separator else ""
    if service == "_hue._tcp":
        return txt.get("bridgeid", "")
    return ""


def _mac_from_hex(text: str) -> Mac | None:
    if len(text) != 12:
        return None
    return _mac_or_none(":".join(text[i : i + 2] for i in range(0, 12, 2)))


def _mac_or_none(text: str) -> Mac | None:
    try:
        return normalise_mac(text, "mdns")
    except Exception:
        return None


def mdns_sightings(instances: Iterable[CachedInstance]) -> tuple[Sighting, ...]:
    """One sighting per (device, address) across the instances, the device being the MAC when the
    records carry one and the hostname otherwise; the newest A record's time wins when several
    instances of one device name the same address, and among equals the `_airplay` instance, whose
    name is the device's AirPlay name."""
    best: dict[tuple[str, str], Sighting] = {}
    for cached in instances:
        if cached.server is None:
            continue
        mac = mac_from_instance(cached.service, cached.txt)
        hostname = canonical_hostname(cached.server)
        identity = mac if mac is not None else f"host:{hostname}"
        for address, seen_at in cached.addresses:
            key = (identity, address)
            current = best.get(key)
            if current is not None and (
                seen_at < current.observed_at
                or (seen_at == current.observed_at and current.extra.get("service") == "_airplay._tcp")
            ):
                continue
            best[key] = Sighting(
                address=address,
                observed_at=seen_at,
                mac=mac,
                hostname=hostname,
                name=cached.instance,
                extra={
                    "service": cached.service,
                    "identifier": device_identifier(cached.service, cached.instance, cached.txt),
                },
            )
    return tuple(best[key] for key in sorted(best))


class _NoopListener(ServiceListener):
    """The browser's purpose is to keep records flowing into the cache; nothing happens per event."""

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass


class AddressBrowser:
    """Browses the MAC-bearing services and reads their records back from the cache."""

    def __init__(self, lan_ip: str) -> None:
        self._zc = Zeroconf(interfaces=[lan_ip], ip_version=IPVersion.V4Only)
        self._types = [service_type(service) for service in MDNS_SERVICES]
        self._browser = ServiceBrowser(self._zc, self._types, _NoopListener())

    def cached_instances(
        self,
        *,
        now: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> tuple[CachedInstance, ...]:
        """Every unexpired instance of the browsed services, with its A records' arrival times in epoch
        seconds (`created` is monotonic milliseconds; both clocks are read here, together)."""
        return cached_instances(self._zc.cache, self._types, now=now, monotonic_ms=monotonic() * 1000)

    def request(self, fqdn: str, timeout_ms: int) -> bool:
        """Ask the network for one instance's records (SRV, TXT, A), refreshing their cache entries if
        the device answers; for a device no source has seen lately."""
        return ServiceInfo(_type_of(fqdn), fqdn).request(self._zc, timeout_ms)

    def close(self) -> None:
        self._browser.cancel()
        self._zc.close()


def cached_instances(
    cache: DNSCache, types: Sequence[str], *, now: float, monotonic_ms: float
) -> tuple[CachedInstance, ...]:
    """Pure read of the cache, separated from the browser so a test can fill a cache and call it."""
    found: list[CachedInstance] = []
    for type_ in types:
        for pointer in cache.get_all_by_details(type_, _TYPE_PTR, _CLASS_IN):
            if not isinstance(pointer, DNSPointer) or pointer.is_expired(monotonic_ms):
                continue
            fqdn = pointer.alias
            records = list(cache.entries_with_name(fqdn))
            services = [r for r in records if isinstance(r, DNSService) and not r.is_expired(monotonic_ms)]
            texts = [r for r in records if isinstance(r, DNSText) and not r.is_expired(monotonic_ms)]
            server = services[0].server if services else None
            addresses: list[tuple[str, float]] = []
            if server is not None:
                for record in cache.entries_with_name(server):
                    if (
                        isinstance(record, DNSAddress)
                        and record.type == _TYPE_A
                        and not record.is_expired(monotonic_ms)
                    ):
                        seen_at = now - (monotonic_ms - record.created) / 1000
                        addresses.append((str(_ipv4(record.address)), seen_at))
            found.append(
                CachedInstance(
                    service=short_service(type_),
                    instance=fqdn.removesuffix("." + type_),
                    fqdn=fqdn,
                    server=server,
                    txt=parse_txt(texts[0].text) if texts else {},
                    addresses=tuple(sorted(addresses)),
                )
            )
    return tuple(found)


def _type_of(fqdn: str) -> str:
    """`"Name._airplay._tcp.local."` -> `"_airplay._tcp.local."` (the last three labels)."""
    return ".".join(fqdn.rstrip(".").split(".")[-3:]) + "."


def _ipv4(packed: bytes) -> str:
    return ".".join(str(b) for b in packed)


def instances_by_hostname(instances: Iterable[CachedInstance]) -> dict[str, CachedInstance]:
    """The `_airplay._tcp` (or failing that, any) instance per canonical hostname, for re-queries."""
    by_hostname: dict[str, CachedInstance] = {}
    for cached in instances:
        if cached.server is None:
            continue
        hostname = canonical_hostname(cached.server)
        if hostname not in by_hostname or cached.service == "_airplay._tcp":
            by_hostname[hostname] = cached
    return by_hostname
