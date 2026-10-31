"""Entry point: `hue-exporter`.

Polls the Philips Hue bridge every 30 s: the CLIP v2 resource list (devices, lights, sensors, rooms)
and the v1 configuration (Zigbee channel, clock, cloud links, last software install). The bridge is
identified by its MAC (the vault's `vault_hue_bridge_mac`, as `MONITORING_HUE_BRIDGE_MAC`), never by
an address: each poll asks the device address inventory for the bridge's freshest address, and a poll
with no fresh address is a failed poll (`hue_up 0`, stage `address`) rather than a guess (2026-10-30:
probes that followed a stale address reported the wrong device).

TLS: the bridge's certificate chains to Signify's published root CA (shipped with this module) and
names the bridge id, which is the MAC with `fffe` in the middle, so the chain and the name are both
verified before the application key is sent anywhere. The key reaches the bridge as a request header
(CLIP v2) and in the v1 path; `hue/client.py` keeps it out of every log line and exception.
"""

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from prometheus_client import CollectorRegistry, Counter

from justdavis_monitoring_exporters.common.device_addresses import (
    DEVICE_ADDRESSES_DIR,
    AddressInventory,
)
from justdavis_monitoring_exporters.common.errors import (
    ExporterError,
    LoginError,
    ParseError,
    ResponseTooLarge,
)
from justdavis_monitoring_exporters.common.http import HttpClient, RequestsHttpClient, private_ca_session
from justdavis_monitoring_exporters.common.loop import start_scrape_thread
from justdavis_monitoring_exporters.common.metrics import ErrorStage, count_error, error_counter
from justdavis_monitoring_exporters.common.problem_log import ProblemLog
from justdavis_monitoring_exporters.common.server import (
    configure_logging,
    load_settings,
    run_until_stopped,
    serve,
)
from justdavis_monitoring_exporters.common.settings import (
    Mac,
    SettingsError,
    env_int,
    env_port,
    env_str,
    normalise_mac,
)
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder
from justdavis_monitoring_exporters.hue.client import FetchError, HueClient
from justdavis_monitoring_exporters.hue.collector import HueCollector
from justdavis_monitoring_exporters.hue.models import HueSnapshot
from justdavis_monitoring_exporters.hue.parser import parse_config, parse_resources

log = logging.getLogger(__name__)

_BACKOFF_CAP_SECONDS = 300
# Signify's root CA for bridge certificates, from Hue's developer documentation ("Using HTTPS");
# verified 2026-10-31 to sign this bridge's certificate.
ROOT_CA_FILE = Path(__file__).with_name("huebridge_cacert.pem")


def bridge_id_for(mac: Mac) -> str:
    """The Hue bridge id is its MAC with `fffe` inserted in the middle: `ec:b5:fa:10:96:3b` is
    `ecb5fafffe10963b` (lowercase, as the certificate's common name spells it)."""
    digits = mac.replace(":", "")
    return f"{digits[:6]}fffe{digits[6:]}"


class NoFreshAddress(ExporterError):
    """No source of the device address inventory has seen the bridge within the freshness window."""


@dataclass(frozen=True, slots=True)
class HueBridge:
    mac: Mac
    application_key: str = field(repr=False)

    @property
    def bridge_id(self) -> str:
        return bridge_id_for(self.mac)


@dataclass(frozen=True, slots=True)
class HueSettings:
    # None is the "not configured" state: serve hue_up 0 and idle.
    bridge: HueBridge | None
    port: int
    listen_addr: str
    interval_seconds: int
    shared_dir: Path
    max_age_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "HueSettings":
        mac_text = env.get("MONITORING_HUE_BRIDGE_MAC", "").strip()
        bridge: HueBridge | None = None
        if mac_text:
            # A trailing newline or space pasted into the vault would turn every poll into a rejected key.
            key = env.get("HUE_APPLICATION_KEY", "").strip()
            if not key:
                raise SettingsError("HUE_APPLICATION_KEY: required when MONITORING_HUE_BRIDGE_MAC is set")
            bridge = HueBridge(mac=normalise_mac(mac_text, "MONITORING_HUE_BRIDGE_MAC"), application_key=key)
        return cls(
            bridge=bridge,
            port=env_port(env, "MONITORING_HUE_PORT", 9805),
            listen_addr=env_str(env, "MONITORING_LISTEN_ADDR", "0.0.0.0"),
            interval_seconds=env_int(env, "MONITORING_HUE_INTERVAL", 30, minimum=1),
            shared_dir=Path(env_str(env, "MONITORING_SHARED_DIR", "/shared")),
            max_age_seconds=env_int(env, "MONITORING_DEVICE_ADDRESS_MAX_AGE", 180, minimum=1),
        )


def build_registry() -> tuple[CollectorRegistry, HueCollector, Counter]:
    """The registry served over HTTP, with the collector and the per-stage error counter on it."""
    registry = CollectorRegistry()
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial())
    collector = HueCollector(statuses)
    registry.register(collector)
    errors = error_counter("hue", ("address", "login", "fetch", "tls", "parse"), registry)
    return registry, collector, errors


def production_http(bridge: HueBridge) -> Callable[[str], HttpClient]:
    """An HTTP client per bridge address, all on one session that verifies the bridge's certificate
    (Signify's root, the bridge id as the name) and carries the application key header."""
    session = private_ca_session(str(ROOT_CA_FILE), bridge.bridge_id)
    session.headers["hue-application-key"] = bridge.application_key

    def for_address(address: str) -> HttpClient:
        return RequestsHttpClient(f"https://{address}", session=session, verify=str(ROOT_CA_FILE))

    return for_address


class HueScraper:
    """One poll: find the bridge's fresh address, fetch both APIs, parse, publish."""

    def __init__(
        self,
        settings: HueSettings,
        bridge: HueBridge,
        collector: HueCollector,
        errors: Counter,
        *,
        http_for_address: Callable[[str], HttpClient],
    ) -> None:
        self._settings = settings
        self._bridge = bridge
        self._collector = collector
        self._errors = errors
        self._http_for_address = http_for_address
        self._inventory_dir = settings.shared_dir / DEVICE_ADDRESSES_DIR
        self._problems = ProblemLog(log)
        self._client: tuple[str, HueClient] | None = None

    def __call__(self) -> None:
        now = time.time()
        address = self._fresh_address(now)
        client = self._client_for(address)
        try:
            resources = client.fetch_resources()
            config = client.fetch_config()
        except LoginError:
            self._fail("login")
            raise
        except FetchError as exc:
            self._fail(exc.stage)
            raise
        except ResponseTooLarge:
            self._fail("fetch")
            raise
        except Exception:
            self._fail("fetch")
            raise
        finally:
            # A fresh connection every poll re-verifies the bridge's certificate each time.
            client.close_connections()
        try:
            devices, lights, sensors = parse_resources(resources)
            bridge = parse_config(config)
        except LoginError:
            self._fail("login")
            raise
        except ParseError:
            self._fail("parse")
            raise
        except Exception:
            self._fail("parse")
            raise
        if bridge.bridge_id.lower() != self._bridge.bridge_id:
            self._fail("parse")
            raise ParseError("config.bridgeid: the device at this address is not the configured bridge")
        self._collector.publish(HueSnapshot(bridge, devices, lights, sensors), address=address)
        log.debug("parsed %d devices, %d lights, %d sensors", len(devices), len(lights), len(sensors))

    def _fresh_address(self, now: float) -> str:
        inventory = AddressInventory.load(self._inventory_dir, problems=self._problems)
        latest = inventory.latest(self._bridge.mac, now=now, max_age=self._settings.max_age_seconds)
        if latest is None:
            self._fail("address")
            self._problems.problem(
                "address",
                "no source of the device address inventory has seen the bridge's address within the "
                "freshness window; not polling it",
            )
            raise NoFreshAddress("no fresh address for the bridge")
        self._problems.clear("address", f"the bridge's address is known again ({latest.source})")
        return latest.address

    def _client_for(self, address: str) -> HueClient:
        if self._client is None or self._client[0] != address:
            if self._client is not None:
                log.info("the bridge's address changed; following it")
            self._client = (
                address,
                HueClient(self._http_for_address(address), application_key=self._bridge.application_key),
            )
        return self._client[1]

    def _fail(self, stage: ErrorStage) -> None:
        count_error(self._errors, stage)
        self._collector.clear()


def main() -> None:
    env = os.environ
    configure_logging(env)
    settings = load_settings(HueSettings.from_env, env)
    registry, collector, errors = build_registry()
    stop = threading.Event()
    shutdown = serve(registry, addr=settings.listen_addr, port=settings.port)
    thread: threading.Thread | None = None
    if settings.bridge is None:
        log.warning("MONITORING_HUE_BRIDGE_MAC is not set; serving hue_up 0 and idling")
    else:
        log.info(
            "polling the Hue bridge %s at whatever address the inventory has for it",
            settings.bridge.bridge_id,
        )
        scraper = HueScraper(
            settings, settings.bridge, collector, errors, http_for_address=production_http(settings.bridge)
        )
        thread = start_scrape_thread(
            "hue-scrape",
            scraper,
            interval_seconds=settings.interval_seconds,
            backoff_cap_seconds=_BACKOFF_CAP_SECONDS,
            stop=stop,
            status=collector.statuses,
        )
    run_until_stopped(scrape_thread=thread, stop=stop, shutdown_server=shutdown)
