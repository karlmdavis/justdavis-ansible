"""Entry point: `airplay-exporter`."""

import logging
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass

import ifaddr
from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.airplay.collector import AirplayCollector
from justdavis_monitoring_exporters.airplay.models import AirplaySnapshot
from justdavis_monitoring_exporters.airplay.resolver import ZeroconfResolver, poll
from justdavis_monitoring_exporters.common.loop import run_scrape_loop
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


def build_registry() -> tuple[CollectorRegistry, AirplayCollector]:
    registry = CollectorRegistry()
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial())
    snapshots: SnapshotHolder[AirplaySnapshot] = SnapshotHolder()
    collector = AirplayCollector(snapshots, statuses)
    registry.register(collector)
    return registry, collector


def main() -> None:
    env = os.environ
    configure_logging(env)
    settings = load_settings(AirplaySettings.from_env, env)
    registry, collector = build_registry()
    stop = threading.Event()
    shutdown = serve(registry, addr=settings.listen_addr, port=settings.port)
    thread: threading.Thread | None = None
    resolver: ZeroconfResolver | None = None
    if settings.lan_ip is None:
        log.warning("MONITORING_LAN_IP is not set; serving airplay_up 0 and idling")
    elif not lan_ip_is_local(settings.lan_ip):
        log.error(
            "MONITORING_LAN_IP %s is not an address of this host; serving airplay_up 0 and idling",
            settings.lan_ip,
        )
    else:
        try:
            resolver = ZeroconfResolver(settings.lan_ip)
        except Exception:
            log.error(
                "could not start mDNS on %s; serving airplay_up 0 and idling", settings.lan_ip, exc_info=True
            )
    if resolver is not None:
        active = resolver

        def scrape() -> None:
            previous = collector.snapshots.get()
            try:
                snapshot = poll(
                    active,
                    settings.tracked_clients,
                    timeout_ms=settings.resolve_timeout_ms,
                    now=time.time(),
                    previous=previous,
                )
            except Exception:
                collector.snapshots.clear()
                raise
            collector.snapshots.set(snapshot)

        thread = threading.Thread(
            target=run_scrape_loop,
            kwargs={
                "scrape": scrape,
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
