"""Tests for the Hue scraper glue: the inventory lookup, both fetches, error attribution."""

import ssl
import time
from pathlib import Path

import pytest
import requests
import urllib3
from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.common.device_addresses import DEVICE_ADDRESSES_DIR, Sighting, SourceStore
from justdavis_monitoring_exporters.common.errors import (
    HttpStatusError,
    LoginError,
    ParseError,
    ResponseTooLarge,
)
from justdavis_monitoring_exporters.common.http import HttpClient
from justdavis_monitoring_exporters.common.settings import Mac
from justdavis_monitoring_exporters.hue.client import FetchError
from justdavis_monitoring_exporters.hue.main import (
    HueBridge,
    HueScraper,
    HueSettings,
    NoFreshAddress,
    bridge_id_for,
    build_registry,
)
from tests.fakes import FailingHttpClient, FakeHttpClient, response

FIXTURES = Path(__file__).parent / "fixtures"
RESOURCES = (FIXTURES / "hue_clip_v2_resource.json").read_bytes()
CONFIG = (FIXTURES / "hue_api_v1_config.json").read_bytes()
PUBLIC_CONFIG = (FIXTURES / "hue_api_v1_config_unauthenticated.json").read_bytes()
MAC = Mac("02:00:00:00:00:30")
KEY = "testkey"
BRIDGE = HueBridge(mac=MAC, application_key=KEY)


def settings(tmp_path: Path) -> HueSettings:
    return HueSettings(
        bridge=BRIDGE,
        port=9805,
        listen_addr="127.0.0.1",
        interval_seconds=30,
        shared_dir=tmp_path,
        max_age_seconds=180,
    )


def inventory_with_bridge(tmp_path: Path, *, age_seconds: float = 5.0, address: str = "192.0.2.21") -> None:
    now = time.time()
    SourceStore(tmp_path / DEVICE_ADDRESSES_DIR, "mdns").record(
        [Sighting(address=address, observed_at=now - age_seconds, mac=MAC, hostname="020000000030")], now=now
    )


def http_with(config: bytes = CONFIG, resources: bytes = RESOURCES) -> FakeHttpClient:
    return FakeHttpClient(
        {
            ("GET", "/clip/v2/resource"): [response(200, resources)],
            ("GET", f"/api/{KEY}/config"): [response(200, config)],
        }
    )


def make(tmp_path: Path, http: HttpClient) -> tuple[HueScraper, CollectorRegistry, list[str]]:
    registry, collector, errors = build_registry()
    addresses: list[str] = []

    def for_address(address: str) -> HttpClient:
        addresses.append(address)
        return http

    return (
        HueScraper(settings(tmp_path), BRIDGE, collector, errors, http_for_address=for_address),
        registry,
        addresses,
    )


def test_bridge_id_is_the_mac_with_fffe_in_the_middle() -> None:
    assert bridge_id_for(Mac("02:00:00:00:00:30")) == "020000fffe000030"


def test_successful_poll_publishes_and_closes_the_connection(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    http = http_with()
    scraper, registry, addresses = make(tmp_path, http)
    scraper()
    assert registry.get_sample_value("hue_bridge_zigbee_channel") == 25.0
    assert (
        registry.get_sample_value(
            "hue_bridge_address_info", {"bridge_id": "020000fffe000030", "address": "192.0.2.21"}
        )
        == 1.0
    )
    assert addresses == ["192.0.2.21"]
    assert http.connection_closes == 1
    scraper()
    # The same address reuses the client; a new address gets a new one.
    assert addresses == ["192.0.2.21"]
    inventory_with_bridge(tmp_path, address="192.0.2.22")
    scraper()
    assert addresses == ["192.0.2.21", "192.0.2.22"]


def published(registry: CollectorRegistry) -> bool:
    return registry.get_sample_value("hue_bridge_zigbee_channel") is not None


def errors(registry: CollectorRegistry) -> dict[str, float]:
    stages = ("address", "login", "fetch", "tls", "parse")
    counts = {s: registry.get_sample_value("hue_scrape_errors_total", {"stage": s}) or 0.0 for s in stages}
    return {stage: count for stage, count in counts.items() if count}


def test_no_fresh_address_fails_the_poll_under_the_address_stage_without_a_request(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    http = http_with()
    scraper, registry, addresses = make(tmp_path, http)
    scraper()
    assert published(registry)
    calls = len(http.calls)
    # The inventory loses the bridge: what was published must go, and nothing is asked of any address.
    (tmp_path / DEVICE_ADDRESSES_DIR / "mdns.json").unlink()
    inventory_with_bridge(tmp_path, age_seconds=600)
    with pytest.raises(NoFreshAddress):
        scraper()
    assert errors(registry) == {"address": 1.0}
    assert not published(registry)
    assert (addresses, len(http.calls)) == (["192.0.2.21"], calls)


def test_a_key_rejected_by_status_counts_login_and_clears_the_snapshot(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    http = FailingHttpClient(http_with().script, error=HttpStatusError(403, "/clip/v2/resource"))
    scraper, registry, _ = make(tmp_path, http)
    scraper()
    http.arm()
    with pytest.raises(LoginError):
        scraper()
    assert errors(registry) == {"login": 1.0}
    assert not published(registry)


def test_a_key_rejected_by_shape_counts_login_and_clears_the_snapshot(tmp_path: Path) -> None:
    # The v1 API answers 200 with the public configuration for a key it does not know.
    inventory_with_bridge(tmp_path)
    http = http_with()
    http.script[("GET", f"/api/{KEY}/config")] = [response(200, CONFIG), response(200, PUBLIC_CONFIG)]
    scraper, registry, _ = make(tmp_path, http)
    scraper()
    with pytest.raises(LoginError):
        scraper()
    assert errors(registry) == {"login": 1.0}
    assert not published(registry)


def test_fetch_failures_count_their_stage_and_clear_the_snapshot(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    http = FailingHttpClient(http_with().script, error=ConnectionError("unreachable"))
    scraper, registry, _ = make(tmp_path, http)
    scraper()
    http.arm()
    with pytest.raises(FetchError):
        scraper()
    assert errors(registry) == {"fetch": 1.0}
    assert not published(registry)
    # The connection is closed after a failed poll too, so the next one verifies the certificate anew.
    assert http.connection_closes == 2


def test_a_certificate_that_is_not_the_bridges_counts_tls_and_clears_the_snapshot(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    wrong_certificate = requests.exceptions.SSLError(
        urllib3.exceptions.SSLError(ssl.SSLCertVerificationError("certificate verify failed"))
    )
    http = FailingHttpClient(http_with().script, error=wrong_certificate)
    scraper, registry, _ = make(tmp_path, http)
    scraper()
    http.arm()
    with pytest.raises(FetchError):
        scraper()
    assert errors(registry) == {"tls": 1.0}
    assert not published(registry)


def test_an_oversized_response_counts_fetch(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    http = FailingHttpClient(http_with().script, error=ResponseTooLarge("too big"))
    scraper, registry, _ = make(tmp_path, http)
    http.arm()
    with pytest.raises(FetchError):
        scraper()
    assert errors(registry) == {"fetch": 1.0}


def test_a_parse_failure_counts_parse_and_clears_the_snapshot(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    http = http_with()
    http.script[("GET", "/clip/v2/resource")] = [response(200, RESOURCES), response(200, b"not json")]
    scraper, registry, _ = make(tmp_path, http)
    scraper()
    with pytest.raises(ParseError):
        scraper()
    assert errors(registry) == {"parse": 1.0}
    assert not published(registry)


def test_a_different_bridge_at_the_address_is_not_published(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    other = CONFIG.replace(b'"020000FFFE000030"', b'"020000FFFE000099"')
    assert other != CONFIG
    http = http_with()
    http.script[("GET", f"/api/{KEY}/config")] = [response(200, CONFIG), response(200, other)]
    scraper, registry, _ = make(tmp_path, http)
    scraper()
    with pytest.raises(ParseError, match="not the configured bridge"):
        scraper()
    assert errors(registry) == {"parse": 1.0}
    assert not published(registry)
    assert registry.get_sample_value("hue_bridge_info", {"bridge_id": "020000fffe000099"}) is None


def test_production_client_verifies_against_the_shipped_root_and_sends_the_key_as_a_header() -> None:
    from justdavis_monitoring_exporters.common.http import PrivateCaAdapter
    from justdavis_monitoring_exporters.hue.main import ROOT_CA_FILE, production_http

    client = production_http(BRIDGE)("192.0.2.21")
    session = client._session  # type: ignore[attr-defined]
    assert session.headers["hue-application-key"] == KEY
    assert session.trust_env is False
    adapter = session.get_adapter("https://192.0.2.21/")
    assert isinstance(adapter, PrivateCaAdapter)
    assert (adapter.ca_file, adapter.server_name) == (str(ROOT_CA_FILE), "020000fffe000030")
    assert client._verify == str(ROOT_CA_FILE)  # type: ignore[attr-defined]
    assert ROOT_CA_FILE.read_text().startswith("-----BEGIN CERTIFICATE-----")
