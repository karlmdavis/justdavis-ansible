"""Tests for the gateway scraper glue: snapshot publication and error attribution."""

import threading
from pathlib import Path

import pytest
import requests
import urllib3
from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.common.errors import LoginError, ParseError
from justdavis_monitoring_exporters.common.loop import run_scrape_loop
from justdavis_monitoring_exporters.gateway.client import GatewayClient, LoginThrottled
from justdavis_monitoring_exporters.gateway.main import GatewayScraper, build_registry
from tests.fakes import FailingHttpClient, FakeClock, FakeHttpClient, response

FIXTURES = Path(__file__).parent / "fixtures"
LOGIN_HTML = (FIXTURES / "gateway_login.html").read_text()
STATUS_HTML = (FIXTURES / "gateway_comcast_network.html").read_text()
# The status page with its tables missing, as the gateway served it once on the first night: a real
# status page (so no re-login), which fails to parse.
HALF_RENDERED_HTML = STATUS_HTML[: STATUS_HTML.index("<table")]


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


def test_every_poll_drops_the_kept_alive_connection_afterwards() -> None:
    http = FakeHttpClient(
        {("GET", "/comcast_network.jst"): [response(200, STATUS_HTML), response(200, HALF_RENDERED_HTML)]}
    )
    scraper, _ = make(http)
    scraper()
    with pytest.raises(ParseError):
        scraper()
    assert http.connection_closes == 2


def test_parse_failure_clears_snapshot_and_counts_parse_stage() -> None:
    http = FakeHttpClient(
        {("GET", "/comcast_network.jst"): [response(200, STATUS_HTML), response(200, HALF_RENDERED_HTML)]}
    )
    scraper, registry = make(http)
    scraper()
    with pytest.raises(ParseError):
        scraper()
    assert registry.get_sample_value("gateway_uptime_seconds") is None
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "parse"}) == 1.0


def test_parse_failure_logs_the_response_framing_but_not_the_body(caplog: pytest.LogCaptureFixture) -> None:
    http = FakeHttpClient(
        {
            ("GET", "/comcast_network.jst"): [
                response(
                    200, HALF_RENDERED_HTML, content_length=str(len(HALF_RENDERED_HTML)), connection="close"
                )
            ]
        }
    )
    scraper, _ = make(http)
    with caplog.at_level("WARNING"), pytest.raises(ParseError):
        scraper()
    line = next(r.getMessage() for r in caplog.records if "did not parse" in r.getMessage())
    assert f"bytes={len(HALF_RENDERED_HTML)}" in line
    assert f"content-length={len(HALF_RENDERED_HTML)}" in line
    assert "connection=close" in line
    assert "System Uptime" not in line


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
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "login"}) == 0.0


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
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "login"}) == 0.0


def _tls_failure(message: str) -> requests.exceptions.SSLError:
    return requests.exceptions.SSLError(urllib3.exceptions.SSLError(message))


def test_fingerprint_mismatch_counts_tls_stage() -> None:
    http = FailingHttpClient(
        {("GET", "/comcast_network.jst"): [response(200, STATUS_HTML)]},
        error=_tls_failure('Fingerprints did not match. Expected "aa", got "bb"'),
    )
    scraper, registry = make(http)
    http.arm()
    with pytest.raises(requests.exceptions.SSLError):
        scraper()
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "tls"}) == 1.0


def test_other_tls_failures_count_fetch_stage() -> None:
    # A connection cut mid-handshake is not "the certificate changed".
    http = FailingHttpClient(
        {("GET", "/comcast_network.jst"): [response(200, STATUS_HTML)]},
        error=_tls_failure("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"),
    )
    scraper, registry = make(http)
    http.arm()
    with pytest.raises(requests.exceptions.SSLError):
        scraper()
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "fetch"}) == 1.0
    assert registry.get_sample_value("gateway_scrape_errors_total", {"stage": "tls"}) == 0.0


def test_failed_poll_serves_up_zero_with_no_device_series_through_the_real_loop() -> None:
    http = FakeHttpClient(
        {("GET", "/comcast_network.jst"): [response(200, STATUS_HTML), response(200, HALF_RENDERED_HTML)]}
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
