"""AirPlay service discovery over mDNS, from the LAN side.

Two signals are collected for each expected HomePod:

* Active resolution (`airplay_service_resolved`): each poll asks the network for the HomePod's
  `_airplay._tcp` and `_raop._tcp` instances with a short timeout. This is the alerting signal, because
  a HomePod that silently stops answering is caught within one poll.
* Passive discovery (`airplay_service_discovered`): a `ServiceBrowser` keeps a cache of announcements.
  It lags real failures by up to the record TTL (75 minutes) but shows what the LAN "sees" without
  being asked, which is the view an iPhone's AirPlay picker has.

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

from justdavis_monitoring_exporters.airplay.models import AirplaySnapshot, ServiceKey
from justdavis_monitoring_exporters.common.settings import TrackedClient

log = logging.getLogger(__name__)

AIRPLAY_TYPE = "_airplay._tcp.local."
RAOP_TYPE = "_raop._tcp.local."
SERVICE_TYPES = (AIRPLAY_TYPE, RAOP_TYPE)
_MAX_WORKERS = 10


def short_service(service_type: str) -> str:
    """`"_airplay._tcp.local."` -> `"_airplay._tcp"`."""
    return service_type.removesuffix(".").removesuffix(".local")


class Resolver(Protocol):
    def resolve(self, service_type: str, instance_name: str, timeout_ms: int) -> bool: ...

    def discovered(self) -> Mapping[ServiceKey, float]: ...


def poll(
    resolver: Resolver, tracked: Sequence[TrackedClient], *, timeout_ms: int, now: float
) -> AirplaySnapshot:
    """Actively resolve every tracked HomePod and merge in passive discovery for the same names."""
    homepods = [client for client in tracked if client.kind == "homepod"]
    expected = {
        ServiceKey(client.airplay_name, short_service(t)) for client in homepods for t in SERVICE_TYPES
    }
    jobs = [(client.airplay_name, service_type) for client in homepods for service_type in SERVICE_TYPES]
    resolved: dict[ServiceKey, bool] = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(jobs))) as pool:
            results = pool.map(lambda job: resolver.resolve(job[1], job[0], timeout_ms), jobs)
            for (name, service_type), ok in zip(jobs, results, strict=True):
                resolved[ServiceKey(name, short_service(service_type))] = ok
    passive = {key: seen for key, seen in resolver.discovered().items() if key in expected}
    last_seen: dict[ServiceKey, float] = dict(passive)
    for key, ok in resolved.items():
        if ok:
            last_seen[key] = now
    return AirplaySnapshot(resolved=resolved, discovered=set(passive), last_seen=last_seen)


class _Listener(ServiceListener):
    """Records the last time each (name, service) announcement was seen; never blocks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[ServiceKey, float] = {}

    def _record(self, type_: str, name: str) -> None:
        instance = name.removesuffix("." + type_)
        with self._lock:
            self._seen[ServiceKey(instance, short_service(type_))] = time.time()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._record(type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._record(type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with self._lock:
            self._seen.pop(ServiceKey(name.removesuffix("." + type_), short_service(type_)), None)

    def snapshot(self) -> dict[ServiceKey, float]:
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

    def discovered(self) -> Mapping[ServiceKey, float]:
        return self._listener.snapshot()

    def close(self) -> None:
        self._browser.cancel()
        self._zc.close()
