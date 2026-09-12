"""Entry point: `gateway-exporter`."""

import logging
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from prometheus_client import Counter

from justdavis_monitoring_exporters.common.errors import LoginError, ParseError
from justdavis_monitoring_exporters.common.http import RequestsHttpClient, pinned_session
from justdavis_monitoring_exporters.common.loop import run_scrape_loop
from justdavis_monitoring_exporters.common.server import (
    configure_logging,
    load_settings,
    run_until_stopped,
    serve,
)
from justdavis_monitoring_exporters.common.settings import SettingsError, env_host, env_int, env_str
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder
from justdavis_monitoring_exporters.gateway.client import GatewayClient, LoginThrottled
from justdavis_monitoring_exporters.gateway.collector import GatewayCollector
from justdavis_monitoring_exporters.gateway.models import GatewayStatus
from justdavis_monitoring_exporters.gateway.parser import parse_comcast_network

log = logging.getLogger(__name__)

# Kept below the gateway GUI's inactivity timeout so the session survives a backoff period.
_BACKOFF_CAP_SECONDS = 300


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    host: str | None
    username: str
    password: str = field(repr=False)
    tls_fingerprint_sha256: str | None
    port: int
    listen_addr: str
    interval_seconds: int
    relogin_min_seconds: int

    @property
    def base_url(self) -> str:
        scheme = "https" if self.tls_fingerprint_sha256 else "http"
        return f"{scheme}://{self.host}"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "GatewaySettings":
        host = env_host(env, "MONITORING_GATEWAY_HOST")
        username = env.get("GATEWAY_USERNAME", "")
        password = env.get("GATEWAY_PASSWORD", "")
        if host is not None and not (username and password):
            raise SettingsError(
                "GATEWAY_USERNAME/GATEWAY_PASSWORD: required when MONITORING_GATEWAY_HOST is set"
            )
        return cls(
            host=host,
            username=username,
            password=password,
            tls_fingerprint_sha256=env_host(env, "MONITORING_GATEWAY_TLS_FINGERPRINT_SHA256"),
            port=env_int(env, "MONITORING_GATEWAY_PORT", 9802),
            listen_addr=env_str(env, "MONITORING_LISTEN_ADDR", "0.0.0.0"),
            interval_seconds=env_int(env, "MONITORING_GATEWAY_INTERVAL", 60),
            relogin_min_seconds=env_int(env, "MONITORING_GATEWAY_RELOGIN_MIN_SECONDS", 300),
        )


class GatewayScraper:
    def __init__(
        self, client: GatewayClient, snapshots: SnapshotHolder[GatewayStatus], errors: Counter
    ) -> None:
        self._client = client
        self._snapshots = snapshots
        self._errors = errors

    def __call__(self) -> None:
        try:
            body = self._client.fetch_comcast_network()
        except LoginThrottled:
            self._snapshots.clear()
            log.debug("gateway session lost; waiting for the re-login window")
            raise
        except LoginError:
            self._errors.labels(stage="login").inc()
            self._snapshots.clear()
            raise
        except Exception:
            self._errors.labels(stage="fetch").inc()
            self._snapshots.clear()
            raise
        try:
            snapshot = parse_comcast_network(body)
        except ParseError:
            self._errors.labels(stage="parse").inc()
            self._snapshots.clear()
            raise
        self._snapshots.set(snapshot)
        log.debug("parsed %d downstream channels", len(snapshot.downstream))


def main() -> None:
    env = os.environ
    configure_logging(env)
    settings = load_settings(GatewaySettings.from_env, env)
    snapshots: SnapshotHolder[GatewayStatus] = SnapshotHolder()
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial())
    errors = Counter("gateway_scrape_errors", "Scrape errors by stage.", ["stage"])
    stop = threading.Event()
    shutdown = serve(GatewayCollector(snapshots, statuses), addr=settings.listen_addr, port=settings.port)
    thread: threading.Thread | None = None
    if settings.host is None:
        log.warning("MONITORING_GATEWAY_HOST is not set; serving gateway_up 0 and idling")
    else:
        if settings.tls_fingerprint_sha256 is not None:
            # Chain/hostname checks are replaced by the fingerprint assertion in the pinned adapter.
            http = RequestsHttpClient(
                settings.base_url, session=pinned_session(settings.tls_fingerprint_sha256), verify=False
            )
        else:
            http = RequestsHttpClient(settings.base_url)
        client = GatewayClient(
            http,
            username=settings.username,
            password=settings.password,
            relogin_min_seconds=settings.relogin_min_seconds,
            clock=time.monotonic,
        )
        thread = threading.Thread(
            target=run_scrape_loop,
            kwargs={
                "scrape": GatewayScraper(client, snapshots, errors),
                "interval_seconds": settings.interval_seconds,
                "backoff_cap_seconds": _BACKOFF_CAP_SECONDS,
                "stop": stop,
                "status": statuses,
                "clock": time.time,
            },
            name="gateway-scrape",
            daemon=True,
        )
    run_until_stopped(scrape_thread=thread, stop=stop, shutdown_server=shutdown)
