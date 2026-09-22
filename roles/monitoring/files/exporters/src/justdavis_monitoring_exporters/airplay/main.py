"""Entry point: `airplay-exporter`."""

import logging
import os
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass

import ifaddr
from prometheus_client import CollectorRegistry, Counter

from justdavis_monitoring_exporters.airplay.collector import AirplayCollector
from justdavis_monitoring_exporters.airplay.models import ServiceKey
from justdavis_monitoring_exporters.airplay.resolver import Resolver, ZeroconfResolver, poll
from justdavis_monitoring_exporters.common.loop import run_scrape_loop
from justdavis_monitoring_exporters.common.metrics import count_error
from justdavis_monitoring_exporters.common.server import (
    configure_logging,
    load_settings,
    run_until_stopped,
    serve,
)
from justdavis_monitoring_exporters.common.settings import (
    TrackedClient,
    env_host,
    env_int,
    env_port,
    env_str,
    parse_tracked_clients,
)
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder

log = logging.getLogger(__name__)

_BACKOFF_CAP_SECONDS = 300


@dataclass(frozen=True, slots=True)
class AirplaySettings:
    lan_ip: str | None
    port: int
    listen_addr: str
    interval_seconds: int
    resolve_timeout_ms: int
    tracked_clients: tuple[TrackedClient, ...]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AirplaySettings":
        return cls(
            lan_ip=env_host(env, "MONITORING_LAN_IP"),
            port=env_port(env, "MONITORING_AIRPLAY_PORT", 9803),
            listen_addr=env_str(env, "MONITORING_LISTEN_ADDR", "0.0.0.0"),
            interval_seconds=env_int(env, "MONITORING_AIRPLAY_INTERVAL", 30, minimum=1),
            resolve_timeout_ms=env_int(env, "MONITORING_AIRPLAY_RESOLVE_TIMEOUT_MS", 3000, minimum=100),
            tracked_clients=parse_tracked_clients(env.get("MONITORING_TRACKED_CLIENTS", "")),
        )


def lan_ip_is_local(lan_ip: str) -> bool:
    """zeroconf silently binds nothing for an address that is not on a local interface, so check first."""
    return any(str(ip.ip) == lan_ip for adapter in ifaddr.get_adapters() for ip in adapter.ips if ip.is_IPv4)


def build_registry() -> tuple[CollectorRegistry, AirplayCollector, Counter]:
    """The registry served over HTTP, with the collector and the per-stage error counter on it."""
    registry = CollectorRegistry()
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial())
    collector = AirplayCollector(statuses)
    registry.register(collector)
    errors = Counter("airplay_scrape_errors", "Scrape errors by stage.", ["stage"], registry=registry)
    return registry, collector, errors


class AirplayScraper:
    """One poll: resolve every HomePod's services and publish the observations.

    The last-seen timestamps are kept here, across polls and across failed polls, so that the
    `airplay_service_last_seen_timestamp_seconds` series survive a resolver outage.
    """

    def __init__(
        self, settings: AirplaySettings, resolver: Resolver, collector: AirplayCollector, errors: Counter
    ) -> None:
        self._settings = settings
        self._resolver = resolver
        self._collector = collector
        self._errors = errors
        self._last_seen: dict[ServiceKey, float] = {}

    def __call__(self) -> None:
        try:
            snapshot = poll(
                self._resolver,
                self._settings.tracked_clients,
                timeout_ms=self._settings.resolve_timeout_ms,
                now=time.time(),
                last_seen=self._last_seen,
            )
        except Exception:
            count_error(self._errors, "resolve")
            self._collector.clear()
            raise
        for key, seen in snapshot.services.items():
            if seen.last_seen is not None:
                self._last_seen[key] = seen.last_seen
        self._collector.publish(snapshot)


def main() -> None:
    env = os.environ
    configure_logging(env)
    settings = load_settings(AirplaySettings.from_env, env)
    registry, collector, errors = build_registry()
    stop = threading.Event()
    shutdown = serve(registry, addr=settings.listen_addr, port=settings.port)
    thread: threading.Thread | None = None
    resolver: ZeroconfResolver | None = None
    if settings.lan_ip is None:
        log.warning("MONITORING_LAN_IP is not set; serving airplay_up 0 and idling")
    else:
        # Both of these can be transient at boot (the LAN address not yet configured), so exit and
        # let the container's restart policy retry rather than idling until someone notices.
        if not lan_ip_is_local(settings.lan_ip):
            log.error("MONITORING_LAN_IP %s is not an address of this host; exiting", settings.lan_ip)
            shutdown()
            sys.exit(1)
        try:
            resolver = ZeroconfResolver(settings.lan_ip)
        except Exception:
            log.error("could not start mDNS on %s; exiting", settings.lan_ip, exc_info=True)
            shutdown()
            sys.exit(1)
        thread = threading.Thread(
            target=run_scrape_loop,
            kwargs={
                "scrape": AirplayScraper(settings, resolver, collector, errors),
                "interval_seconds": settings.interval_seconds,
                "backoff_cap_seconds": _BACKOFF_CAP_SECONDS,
                "stop": stop,
                "status": collector.statuses,
                "clock": time.time,
            },
            name="airplay-scrape",
            daemon=True,
        )
    run_until_stopped(
        scrape_thread=thread,
        stop=stop,
        shutdown_server=shutdown,
        cleanup=resolver.close if resolver is not None else None,
    )
