"""Tests for the gateway scraper glue: snapshot publication and error attribution."""

import threading
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.common.errors import LoginError, ParseError
from justdavis_monitoring_exporters.common.loop import run_scrape_loop
from justdavis_monitoring_exporters.gateway.client import GatewayClient, LoginThrottled
from justdavis_monitoring_exporters.gateway.main import GatewayScraper, build_registry
from tests.fakes import FailingHttpClient, FakeClock, FakeHttpClient, response

FIXTURES = Path(__file__).parent / "fixtures"
LOGIN_HTML = (FIXTURES / "gateway_login.html").read_text()
STATUS_HTML = (FIXTURES / "gateway_comcast_network.html").read_text()


def make(http: FakeHttpClient, clock: FakeClock | None = None) -> tuple[GatewayScraper, CollectorRegistry]:
    registry, collector, errors = build_registry()
    client = GatewayClient(
        http, username="admin", password="pw", relogin_min_seconds=300, clock=clock or FakeClock()
    )
    return GatewayScraper(client, collector, errors), registry


def test_successful_poll_publishes_snapshot() -> None:
    scraper, registry = make(FakeHttpClient({("GET", "/comcast_network.jst"): [response(200, STATUS_HTML)]}))
    scraper()
    assert registry.get_sample_value("gateway_uptime_seconds") == 6149.0


def test_parse_failure_clears_snapshot_and_counts_parse_stage() -> None:
    http = FakeHttpClient(
        {("GET", "/comcast_network.jst"): [response(200, STATUS_HTML), response(200, "<html></html>")]}
    )
    scraper, registry = make(http)
    scraper()
    with pytest.raises(ParseError):
        scraper()
    assert registry.get_sample_value("gateway_uptime_seconds") is None
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "parse"}) == 1.0


def test_rejected_login_counts_login_stage() -> None:
    http = FakeHttpClient(
        {
            ("GET", "/comcast_network.jst"): [response(200, LOGIN_HTML)],
            ("POST", "/check.jst"): [response(200, LOGIN_HTML)],
        }
    )
    scraper, registry = make(http)
    with pytest.raises(LoginError):
        scraper()
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "login"}) == 1.0


def test_throttled_relogin_clears_snapshot_without_counting_an_error() -> None:
    clock = FakeClock()
    http = FakeHttpClient(
        {
            ("GET", "/comcast_network.jst"): [
                response(200, LOGIN_HTML),
                response(200, STATUS_HTML),
                response(200, LOGIN_HTML),
            ],
            ("POST", "/check.jst"): [response(302, "", location="/at_a_glance.jst")],
        }
    )
    scraper, registry = make(http, clock)
    scraper()
    # A human logged in to the GUI and took the single session; the next poll sees the login form and
    # is inside the re-login window, so it must back off quietly.
    with pytest.raises(LoginThrottled):
        scraper()
    assert registry.get_sample_value("gateway_uptime_seconds") is None
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "login"}) is None


def test_unreachable_gateway_clears_snapshot_and_counts_fetch_stage() -> None:
    http = FailingHttpClient(
        {("GET", "/comcast_network.jst"): [response(200, STATUS_HTML)]},
        error=ConnectionError("gateway unreachable"),
    )
    scraper, registry = make(http)
    scraper()
    http.arm()
    with pytest.raises(ConnectionError):
        scraper()
    assert registry.get_sample_value("gateway_uptime_seconds") is None
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "fetch"}) == 1.0
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "login"}) is None


def test_failed_poll_serves_up_zero_with_no_device_series_through_the_real_loop() -> None:
    http = FakeHttpClient(
        {("GET", "/comcast_network.jst"): [response(200, STATUS_HTML), response(200, "<html></html>")]}
    )
    registry, collector, errors = build_registry()
    client = GatewayClient(http, username="admin", password="pw", relogin_min_seconds=300, clock=FakeClock())
    scraper = GatewayScraper(client, collector, errors)
    stop = threading.Event()
    waits = 0

    def wait(_seconds: float) -> bool:
        nonlocal waits
        waits += 1
        if waits == 2:
            stop.set()
        return stop.is_set()

    run_scrape_loop(
        scraper,
        interval_seconds=60,
        backoff_cap_seconds=300,
        stop=stop,
        status=collector.statuses,
        wait=wait,
        jitter=lambda: 1.0,
        clock=lambda: 42.0,
    )
    assert registry.get_sample_value("gateway_up") == 0.0
    assert registry.get_sample_value("gateway_consecutive_failures") == 1.0
    assert registry.get_sample_value("gateway_last_success_timestamp_seconds") == 42.0
    assert registry.get_sample_value("gateway_uptime_seconds") is None
