"""AirPlay service discovery over mDNS, from the LAN side.

Two discovery signals (plus a last-seen timestamp) are collected for each expected HomePod:

* Active resolution (`airplay_service_resolved`): each poll asks the network for the HomePod's
  `_airplay._tcp` and `_raop._tcp` instances with a short timeout. This is the alerting signal, because
  a HomePod that silently stops answering is caught within one poll.
* Passive discovery (`airplay_service_discovered`): a `ServiceBrowser` keeps a cache of announcements.
  It lags real failures by up to the record TTL (75 minutes) but shows what the LAN "sees" without
  being asked, which is the view an iPhone's AirPlay picker has.

The `_airplay._tcp` instance name is the HomePod's AirPlay name, so it can be queried directly. The
`_raop._tcp` instance name carries a device-id prefix (`AABBCCDDEEFF@Kitchen`) that is not known in
advance, so it is taken from passive discovery when available and resolved by its full name.

Discovered names are only exported for the expected HomePods so that arbitrary LAN or guest devices
cannot inject unbounded label values. Requires host networking (multicast on the LAN interface).
"""

import logging
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf

from justdavis_monitoring_exporters.airplay.models import AirplaySnapshot, ServiceKey, short_service
from justdavis_monitoring_exporters.common.settings import TrackedClient

log = logging.getLogger(__name__)

AIRPLAY_TYPE = "_airplay._tcp.local."
RAOP_TYPE = "_raop._tcp.local."
SERVICE_TYPES = (AIRPLAY_TYPE, RAOP_TYPE)
_MAX_WORKERS = 10


class Resolver(Protocol):
    def resolve(self, service_type: str, instance_name: str, timeout_ms: int) -> bool: ...

    def discovered(self) -> Mapping[tuple[str, str], float]:
        """Passively seen (service type, full instance name) pairs and when they were last announced."""
        ...


def _instance_for(
    service_type: str, airplay_name: str, discovered: Mapping[tuple[str, str], float]
) -> str | None:
    if service_type == AIRPLAY_TYPE:
        return airplay_name
    for (seen_type, instance), _ in discovered.items():
        if seen_type == service_type and ServiceKey.from_mdns(seen_type, instance).name == airplay_name:
            return instance
    return None


def _safe_resolve(resolver: Resolver, service_type: str, instance: str, timeout_ms: int) -> bool:
    try:
        return resolver.resolve(service_type, instance, timeout_ms)
    except Exception:
        log.warning("resolving %s failed", service_type, exc_info=True)
        return False


def poll(
    resolver: Resolver,
    tracked: Sequence[TrackedClient],
    *,
    timeout_ms: int,
    now: float,
    previous: AirplaySnapshot | None = None,
) -> AirplaySnapshot:
    """Actively resolve every tracked HomePod and merge in passive discovery for the same names.

    `last_seen` carries forward from `previous` so the timestamp series survives an outage.
    """
    homepods = [client for client in tracked if client.kind == "homepod"]
    expected = {
        ServiceKey(client.airplay_name, short_service(t)) for client in homepods for t in SERVICE_TYPES
    }
    passive = resolver.discovered()
    jobs: list[tuple[ServiceKey, str | None]] = []
    for client in homepods:
        for service_type in SERVICE_TYPES:
            key = ServiceKey(client.airplay_name, short_service(service_type))
            jobs.append((key, _instance_for(service_type, client.airplay_name, passive)))
    resolved: dict[ServiceKey, bool] = {}
    resolvable = [(key, instance) for key, instance in jobs if instance is not None]
    if resolvable:
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(resolvable))) as pool:
            results = pool.map(
                lambda job: _safe_resolve(resolver, job[0].service + ".local.", job[1], timeout_ms),
                resolvable,
            )
            for (key, _instance), ok in zip(resolvable, results, strict=True):
                resolved[key] = ok
    for key, _unresolvable in jobs:
        resolved.setdefault(key, False)
    discovered: dict[ServiceKey, float] = {}
    for (seen_type, instance), seen_at in passive.items():
        key = ServiceKey.from_mdns(seen_type, instance)
        if key in expected:
            discovered[key] = max(seen_at, discovered.get(key, 0.0))
    last_seen: dict[ServiceKey, float] = dict(previous.last_seen) if previous is not None else {}
    last_seen.update(discovered)
    for key, ok in resolved.items():
        if ok:
            last_seen[key] = now
    return AirplaySnapshot(resolved=resolved, discovered=frozenset(discovered), last_seen=last_seen)


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
        self._zc = Zeroconf(interfaces=[lan_ip], ip_version=IPVersion.V4Only)
        self._listener = _Listener()
        self._browser = ServiceBrowser(self._zc, list(SERVICE_TYPES), self._listener)

    def resolve(self, service_type: str, instance_name: str, timeout_ms: int) -> bool:
        info = self._zc.get_service_info(service_type, f"{instance_name}.{service_type}", timeout=timeout_ms)
        return info is not None

    def discovered(self) -> Mapping[tuple[str, str], float]:
        return self._listener.snapshot()

    def close(self) -> None:
        self._browser.cancel()
        self._zc.close()
