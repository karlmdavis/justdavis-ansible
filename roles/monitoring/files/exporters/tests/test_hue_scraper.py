"""Tests for the Hue scraper glue: the inventory lookup, both fetches, error attribution."""

import time
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.common.device_addresses import DEVICE_ADDRESSES_DIR, Sighting, SourceStore
from justdavis_monitoring_exporters.common.errors import HttpStatusError, LoginError, ParseError
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
    assert bridge_id_for(Mac("ec:b5:fa:10:96:3b")) == "ecb5fafffe10963b"


def test_successful_poll_publishes_and_closes_the_connection(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    http = http_with()
    scraper, registry, addresses = make(tmp_path, http)
    scraper()
    assert registry.get_sample_value("hue_bridge_zigbee_channel") == 25.0
    assert (
        registry.get_sample_value(
            "hue_bridge_address_info", {"bridge_id": "020000FFFE000030", "address": "192.0.2.21"}
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


def test_no_fresh_address_fails_the_poll_under_the_address_stage_without_a_request(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path, age_seconds=600)
    http = http_with()
    scraper, registry, addresses = make(tmp_path, http)
    with pytest.raises(NoFreshAddress):
        scraper()
    assert registry.get_sample_value("hue_scrape_errors_total", {"stage": "address"}) == 1.0
    assert registry.get_sample_value("hue_bridge_zigbee_channel") is None
    assert (addresses, http.calls) == ([], [])


def test_rejected_key_counts_login_whether_by_status_or_by_shape(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    scraper, registry, _ = make(tmp_path, http_with(config=PUBLIC_CONFIG))
    with pytest.raises(LoginError):
        scraper()
    http = FailingHttpClient(http_with().script, error=HttpStatusError(403, "/clip/v2/resource"))
    http.arm()
    scraper, registry, _ = make(tmp_path, http)
    with pytest.raises(LoginError):
        scraper()
    assert registry.get_sample_value("hue_scrape_errors_total", {"stage": "login"}) == 1.0


def test_fetch_failures_count_their_stage_and_clear_the_snapshot(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    http = FailingHttpClient(http_with().script, error=ConnectionError("unreachable"))
    scraper, registry, _ = make(tmp_path, http)
    scraper()
    http.arm()
    with pytest.raises(FetchError):
        scraper()
    assert registry.get_sample_value("hue_bridge_zigbee_channel") is None
    assert registry.get_sample_value("hue_scrape_errors_total", {"stage": "fetch"}) == 1.0


def test_parse_failure_and_a_different_bridge_count_parse(tmp_path: Path) -> None:
    inventory_with_bridge(tmp_path)
    scraper, registry, _ = make(tmp_path, http_with(resources=b"not json"))
    with pytest.raises(ParseError):
        scraper()
    assert registry.get_sample_value("hue_scrape_errors_total", {"stage": "parse"}) == 1.0
    other = CONFIG.replace(b'"020000FFFE000030"', b'"020000FFFE000099"')
    scraper, registry, _ = make(tmp_path, http_with(config=other))
    with pytest.raises(ParseError, match="not the configured bridge"):
        scraper()
    assert registry.get_sample_value("hue_scrape_errors_total", {"stage": "parse"}) == 1.0
