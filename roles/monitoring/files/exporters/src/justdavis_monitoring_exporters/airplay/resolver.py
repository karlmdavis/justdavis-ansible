"""AirPlay service discovery over mDNS, from the LAN side.

Three discovery signals (plus a last-seen timestamp) are collected for each expected HomePod:

* Active resolution (`airplay_service_resolved`): each poll asks the network for the HomePod's
  `_airplay._tcp` and `_raop._tcp` instances with a short timeout. This is the alerting signal. A
  HomePod that stops answering is caught once its cached records expire (zeroconf answers from its
  cache first; A records typically live 120 s), which is well within the alert's `for` window.
* Passive discovery (`airplay_service_discovered`): a `ServiceBrowser` keeps a cache of announcements.
  It lags real failures by up to the record TTL (75 minutes) but shows what the LAN "sees" without
  being asked, which is the view an iPhone's AirPlay picker has.
* Unicast reachability (`airplay_service_unicast_resolved`): the `_airplay._tcp` query again, sent
  straight to the HomePod's address (from the AmpliFi exporter's target file) instead of to the
  multicast group. A HomePod that answers unicast but not multicast is up and advertising; the
  multicast path between the wired LAN and its radio has lost it (2026-09-24: seen on 5 GHz clients of
  every access point, never on 2.4 GHz). One that answers neither has its AirPlay service down.

The `_airplay._tcp` instance name is the HomePod's AirPlay name, so it can be queried directly. The
`_raop._tcp` instance name carries a device-id prefix (`AABBCCDDEEFF@Kitchen`) that is not known in
advance, so it is taken from passive discovery when available and resolved by its full name.

A resolver error (as opposed to a query that times out) propagates out of `poll()`, so the exporter
reports a failed poll rather than "every HomePod stopped answering". Discovered names are only exported
for the expected HomePods so that arbitrary LAN or guest devices cannot inject unbounded label values.
Requires host networking (multicast on the LAN interface).
"""

import logging
import socket
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf

# zeroconf does not re-export its wire-level classes or record-type constants.
from zeroconf._dns import DNSQuestion
from zeroconf._protocol.incoming import DNSIncoming
from zeroconf._protocol.outgoing import DNSOutgoing
from zeroconf.const import _CLASS_IN, _FLAGS_QR_QUERY, _TYPE_SRV, _TYPE_TXT

from justdavis_monitoring_exporters.airplay.models import AirplaySnapshot, ServiceKey, ServiceObservation
from justdavis_monitoring_exporters.common.mdns import AIRPLAY_SERVICES, AirplayService, mdns_type
from justdavis_monitoring_exporters.common.settings import TrackedClient

log = logging.getLogger(__name__)

AIRPLAY_TYPE = mdns_type("_airplay._tcp")
RAOP_TYPE = mdns_type("_raop._tcp")
_MAX_WORKERS = 10
_MDNS_PORT = 5353
_MDNS_MAX_PACKET = 9000


class Resolver(Protocol):
    def resolve(self, service_type: str, instance_name: str, timeout_ms: int) -> bool:
        """Actively query for `instance_name` of `service_type`; False when nothing answered in time."""
        ...

    def resolve_unicast(self, ip: str, service_type: str, instance_name: str, timeout_ms: int) -> bool:
        """Query the device at `ip` directly for the instance's records; False when nothing answered."""
        ...

    def discovered(self) -> Mapping[tuple[str, str], float]:
        """Passively seen (service type, full instance name) pairs and when they were last announced."""
        ...


def _instance_for(
    service: AirplayService, airplay_name: str, discovered: Mapping[tuple[str, str], float]
) -> str | None:
    if service == "_airplay._tcp":
        return airplay_name
    for (seen_type, instance), _ in discovered.items():
        if seen_type == mdns_type(service) and ServiceKey.from_mdns(seen_type, instance).name == airplay_name:
            return instance
    return None


def poll(
    resolver: Resolver,
    tracked: Sequence[TrackedClient],
    *,
    addresses: Mapping[str, str],
    timeout_ms: int,
    now: float,
    last_seen: Mapping[ServiceKey, float],
) -> AirplaySnapshot:
    """Actively resolve every tracked HomePod and merge in passive discovery for the same names.

    `addresses` maps tracked-client names (not AirPlay names) to the HomePods' current addresses; the
    `_airplay._tcp` instance of each HomePod with one is also queried by unicast. `last_seen` is the
    caller's record from earlier polls; the returned observations carry it forward, updated by this
    poll's resolutions and announcements.
    """
    homepods = [client for client in tracked if client.kind == "homepod"]
    passive = resolver.discovered()
    jobs: list[tuple[ServiceKey, str | None]] = [
        (
            ServiceKey(client.airplay_name, service),
            _instance_for(service, client.airplay_name, passive),
        )
        for client in homepods
        for service in AIRPLAY_SERVICES
    ]
    resolved: dict[ServiceKey, bool] = {key: False for key, _instance in jobs}
    resolvable = [(key, instance) for key, instance in jobs if instance is not None]
    unicast_jobs: list[tuple[ServiceKey, str]] = [
        (ServiceKey(client.airplay_name, "_airplay._tcp"), ip)
        for client in homepods
        if (ip := addresses.get(client.name)) is not None
    ]
    unicast: dict[ServiceKey, bool] = {}
    if resolvable or unicast_jobs:
        workers = min(_MAX_WORKERS, len(resolvable) + len(unicast_jobs))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            multicast_results = pool.map(
                lambda job: resolver.resolve(mdns_type(job[0].service), job[1], timeout_ms), resolvable
            )
            unicast_results = pool.map(
                lambda job: resolver.resolve_unicast(job[1], AIRPLAY_TYPE, job[0].name, timeout_ms),
                unicast_jobs,
            )
            for (key, _instance), ok in zip(resolvable, multicast_results, strict=True):
                resolved[key] = ok
            for (key, _ip), ok in zip(unicast_jobs, unicast_results, strict=True):
                unicast[key] = ok
    announced: dict[ServiceKey, float] = {}
    for (seen_type, instance), seen_at in passive.items():
        try:
            key = ServiceKey.from_mdns(seen_type, instance)
        except ValueError:
            continue
        if key in resolved:
            announced[key] = max(seen_at, announced.get(key, 0.0))
    services: dict[ServiceKey, ServiceObservation] = {}
    for key, ok in resolved.items():
        seen = last_seen.get(key)
        if key in announced:
            seen = max(announced[key], seen or 0.0)
        if ok or unicast.get(key):
            seen = now
        services[key] = ServiceObservation(
            resolved=ok, discovered=key in announced, last_seen=seen, unicast_resolved=unicast.get(key)
        )
    return AirplaySnapshot(services=services)


class _Listener(ServiceListener):
    """Records the last time each (type, instance) announcement was seen; never blocks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[tuple[str, str], float] = {}

    def _record(self, type_: str, name: str) -> None:
        with self._lock:
            self._seen[(type_, name.removesuffix("." + type_))] = time.time()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._record(type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._record(type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with self._lock:
            self._seen.pop((type_, name.removesuffix("." + type_)), None)

    def snapshot(self) -> dict[tuple[str, str], float]:
        with self._lock:
            return dict(self._seen)


class ZeroconfResolver:
    def __init__(self, lan_ip: str) -> None:
        self._lan_ip = lan_ip
        self._zc = Zeroconf(interfaces=[lan_ip], ip_version=IPVersion.V4Only)
        self._listener = _Listener()
        self._browser = ServiceBrowser(self._zc, [mdns_type(s) for s in AIRPLAY_SERVICES], self._listener)

    def resolve(self, service_type: str, instance_name: str, timeout_ms: int) -> bool:
        info = self._zc.get_service_info(service_type, f"{instance_name}.{service_type}", timeout=timeout_ms)
        return info is not None

    def resolve_unicast(self, ip: str, service_type: str, instance_name: str, timeout_ms: int) -> bool:
        """One SRV+TXT query for the instance, sent from the LAN address to `ip`:5353 on a throwaway
        socket (so it bypasses zeroconf's cache and the multicast group entirely); True when the reply
        carries at least one answer. A closed port (ICMP unreachable) counts as no answer."""
        fqdn = f"{instance_name}.{service_type}"
        outgoing = DNSOutgoing(_FLAGS_QR_QUERY)
        outgoing.add_question(DNSQuestion(fqdn, _TYPE_SRV, _CLASS_IN))
        outgoing.add_question(DNSQuestion(fqdn, _TYPE_TXT, _CLASS_IN))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((self._lan_ip, 0))
            sock.settimeout(timeout_ms / 1000)
            try:
                for packet in outgoing.packets():
                    sock.sendto(packet, (ip, _MDNS_PORT))
                data, _ = sock.recvfrom(_MDNS_MAX_PACKET)
            except (TimeoutError, ConnectionRefusedError):
                return False
        return bool(DNSIncoming(data).answers())

    def discovered(self) -> Mapping[tuple[str, str], float]:
        return self._listener.snapshot()

    def close(self) -> None:
        self._browser.cancel()
        self._zc.close()
