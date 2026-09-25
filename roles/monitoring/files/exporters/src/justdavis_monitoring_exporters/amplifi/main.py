"""Entry point: `amplifi-exporter`."""

import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from prometheus_client import CollectorRegistry, Counter

from justdavis_monitoring_exporters.amplifi.client import AmplifiClient
from justdavis_monitoring_exporters.amplifi.collector import AmplifiCollector, CounterKey
from justdavis_monitoring_exporters.amplifi.models import AmplifiSnapshot
from justdavis_monitoring_exporters.amplifi.parser import parse_info_async
from justdavis_monitoring_exporters.amplifi.targets import (
    AIRPLAY_TARGETS_FILE,
    PING_TARGETS_FILE,
    PingConfig,
    render_airplay_targets,
    render_ping_targets,
    target_infos,
)
from justdavis_monitoring_exporters.common.counters import Unwrapper32
from justdavis_monitoring_exporters.common.errors import LoginError
from justdavis_monitoring_exporters.common.files import write_if_changed
from justdavis_monitoring_exporters.common.http import RequestsHttpClient
from justdavis_monitoring_exporters.common.loop import start_scrape_thread
from justdavis_monitoring_exporters.common.metrics import ErrorStage, count_error, error_counter
from justdavis_monitoring_exporters.common.server import (
    configure_logging,
    load_settings,
    run_until_stopped,
    serve,
)
from justdavis_monitoring_exporters.common.settings import (
    SettingsError,
    TrackedClient,
    env_host,
    env_int,
    env_port,
    env_str,
    parse_static_targets,
    parse_tracked_clients,
)
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder

log = logging.getLogger(__name__)

_BACKOFF_CAP_SECONDS = 600


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
    static_ping_targets: tuple[str, ...]
    shared_dir: Path
    ping: PingConfig

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
            static_ping_targets=parse_static_targets(env.get("MONITORING_STATIC_PING_TARGETS", "")),
            shared_dir=Path(env_str(env, "MONITORING_SHARED_DIR", "/shared")),
            ping=PingConfig(
                interval_seconds=env_int(env, "MONITORING_PING_INTERVAL", 1, minimum=1),
                timeout_seconds=env_int(env, "MONITORING_PING_TIMEOUT", 2, minimum=1),
                history_size=env_int(env, "MONITORING_PING_HISTORY_SIZE", 60, minimum=1),
            ),
        )


def build_registry(tracked: tuple[TrackedClient, ...]) -> tuple[CollectorRegistry, AmplifiCollector, Counter]:
    """The registry served over HTTP, with the collector and the per-stage error counter on it."""
    registry = CollectorRegistry()
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial())
    collector = AmplifiCollector(statuses, tracked, Unwrapper32[CounterKey]())
    registry.register(collector)
    errors = error_counter("amplifi", ("login", "fetch", "parse", "write"), registry)
    return registry, collector, errors


class AmplifiScraper:
    """One poll: fetch, parse, publish the snapshot, and refresh the dynamic target files.

    A target-file write failure is reported through `amplifi_target_files_ok` and the `write` error
    stage but does not fail the poll, since the router data itself is fine.
    """

    def __init__(
        self, settings: AmplifiSettings, client: AmplifiClient, collector: AmplifiCollector, errors: Counter
    ) -> None:
        self._settings = settings
        self._client = client
        self._collector = collector
        self._errors = errors

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
        self._write_targets(snapshot)

    def _fail(self, stage: ErrorStage) -> None:
        count_error(self._errors, stage)
        self._collector.clear()

    def _write_targets(self, snapshot: AmplifiSnapshot) -> None:
        settings = self._settings
        infos = target_infos(settings.static_ping_targets, snapshot, settings.tracked_clients)
        self._collector.set_targets(infos)
        try:
            if write_if_changed(
                settings.shared_dir / PING_TARGETS_FILE, render_ping_targets(settings.ping, infos)
            ):
                log.info("rewrote %s with %d targets", PING_TARGETS_FILE, len(infos))
            airplay = render_airplay_targets(snapshot, settings.tracked_clients)
            if write_if_changed(settings.shared_dir / AIRPLAY_TARGETS_FILE, airplay):
                log.info("rewrote %s", AIRPLAY_TARGETS_FILE)
        except OSError:
            count_error(self._errors, "write")
            self._collector.set_target_files_ok(False)
            log.error("could not write target files in %s", settings.shared_dir, exc_info=True)
            return
        self._collector.set_target_files_ok(True)

    def seed_targets(self) -> None:
        """Create the target files with static targets only when they do not exist yet.

        Existing files are left alone so that the last known dynamic targets survive a restart until
        the first successful poll replaces them.
        """
        settings = self._settings
        infos = target_infos(settings.static_ping_targets, None, settings.tracked_clients)
        seeds = {
            settings.shared_dir / PING_TARGETS_FILE: render_ping_targets(settings.ping, infos),
            settings.shared_dir / AIRPLAY_TARGETS_FILE: render_airplay_targets(
                None, settings.tracked_clients
            ),
        }
        for path, content in seeds.items():
            if not path.exists():
                write_if_changed(path, content)
                log.info("seeded %s", path.name)


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
            RequestsHttpClient(settings.device.base_url), password=settings.device.password
        )
        scraper = AmplifiScraper(settings, client, collector, errors)
        try:
            scraper.seed_targets()
        except OSError:
            count_error(errors, "write")
            collector.set_target_files_ok(False)
            log.error("could not seed target files in %s", settings.shared_dir, exc_info=True)
        thread = start_scrape_thread(
            "amplifi-scrape",
            scraper,
            interval_seconds=settings.interval_seconds,
            backoff_cap_seconds=_BACKOFF_CAP_SECONDS,
            stop=stop,
            status=collector.statuses,
        )
    run_until_stopped(scrape_thread=thread, stop=stop, shutdown_server=shutdown)
