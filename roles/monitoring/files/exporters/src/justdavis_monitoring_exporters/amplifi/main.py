"""Entry point: `amplifi-exporter`."""

import logging
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from prometheus_client import CollectorRegistry, Counter

from justdavis_monitoring_exporters.amplifi.client import AmplifiClient
from justdavis_monitoring_exporters.amplifi.collector import AmplifiCollector, ByteCounterKey
from justdavis_monitoring_exporters.amplifi.models import AmplifiSnapshot
from justdavis_monitoring_exporters.amplifi.parser import parse_info_async
from justdavis_monitoring_exporters.amplifi.sightings import router_sightings
from justdavis_monitoring_exporters.common.counters import ClientByteTotals
from justdavis_monitoring_exporters.common.device_addresses import DEVICE_ADDRESSES_DIR, SourceStore
from justdavis_monitoring_exporters.common.errors import LoginError
from justdavis_monitoring_exporters.common.http import RequestsHttpClient, lan_session
from justdavis_monitoring_exporters.common.loop import start_scrape_thread
from justdavis_monitoring_exporters.common.metrics import ErrorStage, count_error, error_counter
from justdavis_monitoring_exporters.common.server import (
    configure_logging,
    load_settings,
    run_until_stopped,
    serve,
)
from justdavis_monitoring_exporters.common.settings import (
    Mac,
    SettingsError,
    TrackedClient,
    env_host,
    env_int,
    env_port,
    env_str,
    parse_tracked_clients,
)
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder

log = logging.getLogger(__name__)

_BACKOFF_CAP_SECONDS = 600
# A client off the WiFi for this many polls (an hour at the default 30 s interval) has its byte totals
# forgotten, so rotating private MAC addresses do not accumulate state.
_FORGET_ABSENT_CLIENT_AFTER_POLLS = 120


@dataclass(frozen=True, slots=True)
class AmplifiDevice:
    """The router to poll; its web UI is plain HTTP only."""

    host: str
    password: str = field(repr=False)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}"


@dataclass(frozen=True, slots=True)
class AmplifiSettings:
    # None is the "not configured" state: serve amplifi_up 0 and idle.
    device: AmplifiDevice | None
    port: int
    listen_addr: str
    interval_seconds: int
    tracked_clients: tuple[TrackedClient, ...]
    # Where the exporter writes its device address observations (device-addresses/router.json).
    shared_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AmplifiSettings":
        host = env_host(env, "MONITORING_AMPLIFI_HOST")
        device: AmplifiDevice | None = None
        if host is not None:
            password = env.get("AMPLIFI_PASSWORD", "")
            if not password:
                raise SettingsError("AMPLIFI_PASSWORD: required when MONITORING_AMPLIFI_HOST is set")
            device = AmplifiDevice(host=host, password=password)
        return cls(
            device=device,
            port=env_port(env, "MONITORING_AMPLIFI_PORT", 9801),
            listen_addr=env_str(env, "MONITORING_LISTEN_ADDR", "0.0.0.0"),
            interval_seconds=env_int(env, "MONITORING_AMPLIFI_INTERVAL", 30, minimum=1),
            tracked_clients=parse_tracked_clients(env.get("MONITORING_TRACKED_CLIENTS", "")),
            shared_dir=Path(env_str(env, "MONITORING_SHARED_DIR", "/shared")),
        )


def build_registry(tracked: tuple[TrackedClient, ...]) -> tuple[CollectorRegistry, AmplifiCollector, Counter]:
    """The registry served over HTTP, with the collector and the per-stage error counter on it."""
    registry = CollectorRegistry()
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial())
    byte_totals: ClientByteTotals[ByteCounterKey, Mac] = ClientByteTotals(_FORGET_ABSENT_CLIENT_AFTER_POLLS)
    collector = AmplifiCollector(statuses, tracked, byte_totals)
    registry.register(collector)
    errors = error_counter("amplifi", ("login", "fetch", "parse", "write"), registry)
    return registry, collector, errors


class AmplifiScraper:
    """One poll: fetch, parse, publish the snapshot, and record the router's device sightings in the
    address inventory (the probe target files are rendered from that inventory by the LAN discovery
    exporter)."""

    def __init__(
        self, settings: AmplifiSettings, client: AmplifiClient, collector: AmplifiCollector, errors: Counter
    ) -> None:
        self._settings = settings
        self._client = client
        self._collector = collector
        self._errors = errors
        self._router_store = SourceStore(settings.shared_dir / DEVICE_ADDRESSES_DIR, "router")

    def __call__(self) -> None:
        try:
            body = self._client.fetch_info_async()
        except LoginError:
            self._fail("login")
            raise
        except Exception:
            self._fail("fetch")
            raise
        try:
            snapshot = parse_info_async(body)
            self._collector.publish(snapshot)
        except Exception:
            self._fail("parse")
            raise
        log.debug("parsed %d clients, %d mesh points", len(snapshot.clients), len(snapshot.mesh_points))
        self._record_sightings(snapshot)

    def _record_sightings(self, snapshot: AmplifiSnapshot) -> None:
        """The router's view of every device's address goes to the inventory as the `router` source; a
        write failure is reported through `amplifi_address_source_ok` and the `write` stage, and does
        not fail the poll."""
        now = time.time()
        try:
            self._router_store.record(router_sightings(snapshot, now=now), now=now)
        except OSError:
            count_error(self._errors, "write")
            self._collector.set_address_source_ok(False)
            log.error("could not write %s", self._router_store.path, exc_info=True)
            return
        self._collector.set_address_source_ok(True)

    def _fail(self, stage: ErrorStage) -> None:
        count_error(self._errors, stage)
        self._collector.clear()


def main() -> None:
    env = os.environ
    configure_logging(env)
    settings = load_settings(AmplifiSettings.from_env, env)
    registry, collector, errors = build_registry(settings.tracked_clients)
    stop = threading.Event()
    shutdown = serve(registry, addr=settings.listen_addr, port=settings.port)
    thread: threading.Thread | None = None
    if settings.device is None:
        log.warning("MONITORING_AMPLIFI_HOST is not set; serving amplifi_up 0 and idling")
    else:
        client = AmplifiClient(
            RequestsHttpClient(settings.device.base_url, session=lan_session()),
            password=settings.device.password,
        )
        scraper = AmplifiScraper(settings, client, collector, errors)
        thread = start_scrape_thread(
            "amplifi-scrape",
            scraper,
            interval_seconds=settings.interval_seconds,
            backoff_cap_seconds=_BACKOFF_CAP_SECONDS,
            stop=stop,
            status=collector.statuses,
        )
    run_until_stopped(scrape_thread=thread, stop=stop, shutdown_server=shutdown)
