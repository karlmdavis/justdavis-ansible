"""Entry point: `gateway-exporter`."""

import logging
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

import requests
from prometheus_client import CollectorRegistry, Counter

from justdavis_monitoring_exporters.common.errors import LoginError
from justdavis_monitoring_exporters.common.http import RequestsHttpClient, pinned_session
from justdavis_monitoring_exporters.common.loop import start_scrape_thread
from justdavis_monitoring_exporters.common.metrics import ErrorStage, count_error
from justdavis_monitoring_exporters.common.server import (
    configure_logging,
    load_settings,
    run_until_stopped,
    serve,
)
from justdavis_monitoring_exporters.common.settings import (
    SettingsError,
    env_host,
    env_int,
    env_port,
    env_str,
    parse_fingerprint,
)
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder
from justdavis_monitoring_exporters.gateway.client import GatewayClient, LoginThrottled
from justdavis_monitoring_exporters.gateway.collector import GatewayCollector
from justdavis_monitoring_exporters.gateway.parser import parse_comcast_network

log = logging.getLogger(__name__)

# Kept below the gateway GUI's inactivity timeout (observed to be at least 10 minutes) so the session
# survives a backoff period.
_BACKOFF_CAP_SECONDS = 300


@dataclass(frozen=True, slots=True)
class GatewayDevice:
    """A gateway that can be polled: address, credentials, and the certificate fingerprint to pin.

    The gateway is only ever spoken to over HTTPS with its certificate pinned, so a device without a
    fingerprint cannot exist as this type; see `UnpinnedGateway`.
    """

    host: str
    username: str
    password: str = field(repr=False)
    tls_fingerprint_sha256: str

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"


@dataclass(frozen=True, slots=True)
class UnpinnedGateway:
    """A gateway address without a certificate fingerprint: known, but not to be spoken to (that would
    mean an unauthenticated TLS peer), so the exporter idles with gateway_up 0 until a redeploy records
    one."""

    host: str


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    # None is the "not configured" state: serve gateway_up 0 and idle.
    device: GatewayDevice | UnpinnedGateway | None
    port: int
    listen_addr: str
    interval_seconds: int
    relogin_min_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "GatewaySettings":
        host = env_host(env, "MONITORING_GATEWAY_HOST")
        device: GatewayDevice | UnpinnedGateway | None = None
        if host is not None:
            username = env.get("GATEWAY_USERNAME", "")
            password = env.get("GATEWAY_PASSWORD", "")
            if not (username and password):
                raise SettingsError(
                    "GATEWAY_USERNAME/GATEWAY_PASSWORD: required when MONITORING_GATEWAY_HOST is set"
                )
            fingerprint = parse_fingerprint(
                env.get("MONITORING_GATEWAY_TLS_FINGERPRINT_SHA256", ""),
                "MONITORING_GATEWAY_TLS_FINGERPRINT_SHA256",
            )
            if fingerprint is None:
                device = UnpinnedGateway(host=host)
            else:
                device = GatewayDevice(
                    host=host, username=username, password=password, tls_fingerprint_sha256=fingerprint
                )
        return cls(
            device=device,
            port=env_port(env, "MONITORING_GATEWAY_PORT", 9802),
            listen_addr=env_str(env, "MONITORING_LISTEN_ADDR", "0.0.0.0"),
            interval_seconds=env_int(env, "MONITORING_GATEWAY_INTERVAL", 60, minimum=1),
            relogin_min_seconds=env_int(env, "MONITORING_GATEWAY_RELOGIN_MIN_SECONDS", 300, minimum=0),
        )


def build_registry() -> tuple[CollectorRegistry, GatewayCollector, Counter]:
    """The registry served over HTTP, with the collector and the per-stage error counter on it."""
    registry = CollectorRegistry()
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial())
    collector = GatewayCollector(statuses)
    registry.register(collector)
    errors = Counter("gateway_scrape_errors", "Scrape errors by stage.", ["stage"], registry=registry)
    return registry, collector, errors


class GatewayScraper:
    def __init__(self, client: GatewayClient, collector: GatewayCollector, errors: Counter) -> None:
        self._client = client
        self._collector = collector
        self._errors = errors

    def __call__(self) -> None:
        try:
            body = self._client.fetch_comcast_network()
        except LoginThrottled:
            self._collector.clear()
            log.info(
                "gateway login needed but throttled (session lost, or the last login was rejected); "
                "waiting for the re-login window"
            )
            raise
        except LoginError:
            self._fail("login")
            raise
        except requests.exceptions.SSLError:
            # The pinned fingerprint no longer matches: the certificate changed (re-run the role) or
            # something else answered.
            self._fail("tls")
            raise
        except Exception:
            self._fail("fetch")
            raise
        try:
            snapshot = parse_comcast_network(body)
            self._collector.publish(snapshot)
        except Exception:
            self._fail("parse")
            raise
        log.debug("parsed %d downstream channels", len(snapshot.downstream))

    def _fail(self, stage: ErrorStage) -> None:
        count_error(self._errors, stage)
        self._collector.clear()


def main() -> None:
    env = os.environ
    configure_logging(env)
    settings = load_settings(GatewaySettings.from_env, env)
    registry, collector, errors = build_registry()
    stop = threading.Event()
    shutdown = serve(registry, addr=settings.listen_addr, port=settings.port)
    thread: threading.Thread | None = None
    device = settings.device
    if device is None:
        log.warning("MONITORING_GATEWAY_HOST is not set; serving gateway_up 0 and idling")
    elif isinstance(device, UnpinnedGateway):
        log.error(
            "MONITORING_GATEWAY_TLS_FINGERPRINT_SHA256 is not set; refusing to talk to %s without "
            "certificate pinning, serving gateway_up 0 and idling (redeploy with the gateway reachable)",
            device.host,
        )
    else:
        # Chain/hostname checks are replaced by the fingerprint assertion in the pinned adapter.
        http = RequestsHttpClient(
            device.base_url, session=pinned_session(device.tls_fingerprint_sha256), verify=False
        )
        log.info("polling %s with certificate pinning", device.base_url)
        client = GatewayClient(
            http,
            username=device.username,
            password=device.password,
            relogin_min_seconds=settings.relogin_min_seconds,
            clock=time.monotonic,
        )
        thread = start_scrape_thread(
            "gateway-scrape",
            GatewayScraper(client, collector, errors),
            interval_seconds=settings.interval_seconds,
            backoff_cap_seconds=_BACKOFF_CAP_SECONDS,
            stop=stop,
            status=collector.statuses,
        )
    run_until_stopped(scrape_thread=thread, stop=stop, shutdown_server=shutdown)
