"""Tests for the Comcast gateway client (session reuse, rate-limited re-login)."""

from pathlib import Path

import pytest

from justdavis_monitoring_exporters.common.errors import LoginError
from justdavis_monitoring_exporters.gateway.client import GatewayClient, LoginThrottled
from tests.fakes import FakeClock, FakeHttpClient, response

FIXTURES = Path(__file__).parent / "fixtures"
LOGIN_HTML = (FIXTURES / "gateway_login.html").read_text()
STATUS_HTML = (FIXTURES / "gateway_comcast_network.html").read_text()


def fresh_session_http() -> FakeHttpClient:
    return FakeHttpClient(
        {
            ("GET", "/comcast_network.jst"): [response(200, LOGIN_HTML), response(200, STATUS_HTML)],
            ("POST", "/check.jst"): [response(302, "", location="/at_a_glance.jst")],
        }
    )


def test_login_page_triggers_login_then_retry() -> None:
    http = fresh_session_http()
    client = GatewayClient(http, username="admin", password="pw", relogin_min_seconds=300, clock=FakeClock())
    body = client.fetch_comcast_network()
    assert body == STATUS_HTML.encode()
    assert [(c.method, c.path) for c in http.calls] == [
        ("GET", "/comcast_network.jst"),
        ("POST", "/check.jst"),
        ("GET", "/comcast_network.jst"),
    ]
    assert http.calls[1].data == {"username": "admin", "password": "pw"}


def test_live_session_fetches_without_logging_in() -> None:
    http = FakeHttpClient({("GET", "/comcast_network.jst"): [response(200, STATUS_HTML)]})
    client = GatewayClient(http, username="admin", password="pw", relogin_min_seconds=300, clock=FakeClock())
    client.fetch_comcast_network()
    client.fetch_comcast_network()
    assert all(c.path == "/comcast_network.jst" for c in http.calls)


def test_relogin_is_throttled_to_avoid_kicking_a_human_out() -> None:
    clock = FakeClock()
    http = FakeHttpClient(
        {
            ("GET", "/comcast_network.jst"): [response(200, LOGIN_HTML)],
            ("POST", "/check.jst"): [response(302, "", location="/at_a_glance.jst")],
        }
    )
    client = GatewayClient(http, username="admin", password="pw", relogin_min_seconds=300, clock=clock)
    with pytest.raises(LoginError):
        client.fetch_comcast_network()
    logins_before = sum(1 for c in http.calls if c.path == "/check.jst")
    clock.advance(60)
    with pytest.raises(LoginThrottled):
        client.fetch_comcast_network()
    assert sum(1 for c in http.calls if c.path == "/check.jst") == logins_before


def test_relogin_allowed_once_the_throttle_window_passes() -> None:
    clock = FakeClock()
    http = FakeHttpClient(
        {
            ("GET", "/comcast_network.jst"): [
                response(200, LOGIN_HTML),
                response(200, LOGIN_HTML),
                response(200, LOGIN_HTML),
                response(200, STATUS_HTML),
            ],
            ("POST", "/check.jst"): [response(302, "", location="/at_a_glance.jst")],
        }
    )
    client = GatewayClient(http, username="admin", password="pw", relogin_min_seconds=300, clock=clock)
    with pytest.raises(LoginError):
        client.fetch_comcast_network()
    clock.advance(301)
    assert client.fetch_comcast_network() == STATUS_HTML.encode()


def test_rejected_credentials_raise_login_error() -> None:
    http = FakeHttpClient(
        {
            ("GET", "/comcast_network.jst"): [response(200, LOGIN_HTML)],
            ("POST", "/check.jst"): [response(200, LOGIN_HTML)],
        }
    )
    client = GatewayClient(http, username="admin", password="bad", relogin_min_seconds=0, clock=FakeClock())
    with pytest.raises(LoginError):
        client.fetch_comcast_network()


def test_password_never_appears_in_repr() -> None:
    client = GatewayClient(
        fresh_session_http(), username="admin", password="hunter2", relogin_min_seconds=0, clock=FakeClock()
    )
    assert "hunter2" not in repr(client)
