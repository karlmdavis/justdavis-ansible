"""Tests for the AmpliFi web-UI client (login + info-async fetch)."""

from pathlib import Path

import pytest

from justdavis_monitoring_exporters.amplifi.client import AmplifiClient
from justdavis_monitoring_exporters.common.errors import LoginError
from tests.fakes import FakeHttpClient, response

FIXTURES = Path(__file__).parent / "fixtures"
LOGIN_HTML = (FIXTURES / "amplifi_login.html").read_text()
INFO_HTML = (FIXTURES / "amplifi_info.html").read_text()
INFO_JSON = (FIXTURES / "amplifi_info_async.json").read_bytes()

LOGIN_TOKEN = "AbCdEfGhIjKlMnOp"
INFO_TOKEN = "QrStUvWxYz012345"


def logged_out_then_in() -> FakeHttpClient:
    return FakeHttpClient(
        {
            ("GET", "/login.php"): [response(200, LOGIN_HTML)],
            ("POST", "/login.php"): [
                response(302, "", location="/index.php", set_cookie="webui-session=abc")
            ],
            ("GET", "/info.php"): [response(302, "", location="/login.php"), response(200, INFO_HTML)],
            ("POST", "/info-async.php"): [response(200, INFO_JSON)],
        }
    )


def test_first_fetch_logs_in_using_the_login_page_token() -> None:
    http = logged_out_then_in()
    client = AmplifiClient(http, password="secret")
    body = client.fetch_info_async()
    assert body == INFO_JSON
    login_post = next(c for c in http.calls if c.method == "POST" and c.path == "/login.php")
    assert login_post.data == {"token": LOGIN_TOKEN, "password": "secret"}


def test_fetch_posts_the_info_page_token() -> None:
    http = logged_out_then_in()
    AmplifiClient(http, password="secret").fetch_info_async()
    fetch = next(c for c in http.calls if c.path == "/info-async.php")
    assert fetch.data == {"do": "full", "token": INFO_TOKEN}


def test_subsequent_fetch_reuses_the_session_without_logging_in_again() -> None:
    http = logged_out_then_in()
    client = AmplifiClient(http, password="secret")
    client.fetch_info_async()
    before = len(http.calls)
    client.fetch_info_async()
    new_calls = http.calls[before:]
    assert [c.path for c in new_calls] == ["/info.php", "/info-async.php"]


def test_login_failure_raises_login_error() -> None:
    http = FakeHttpClient(
        {
            ("GET", "/login.php"): [response(200, LOGIN_HTML)],
            ("POST", "/login.php"): [response(200, LOGIN_HTML)],
            ("GET", "/info.php"): [response(302, "", location="/login.php")],
        }
    )
    with pytest.raises(LoginError):
        AmplifiClient(http, password="wrong").fetch_info_async()


def test_missing_login_token_raises_login_error() -> None:
    http = FakeHttpClient(
        {
            ("GET", "/login.php"): [response(200, "<html>no token here</html>")],
            ("GET", "/info.php"): [response(302, "", location="/login.php")],
        }
    )
    with pytest.raises(LoginError):
        AmplifiClient(http, password="secret").fetch_info_async()


def test_password_never_appears_in_repr() -> None:
    client = AmplifiClient(logged_out_then_in(), password="hunter2")
    assert "hunter2" not in repr(client)


def test_login_form_returned_with_200_from_info_page_triggers_login() -> None:
    http = FakeHttpClient(
        {
            ("GET", "/login.php"): [response(200, LOGIN_HTML)],
            ("POST", "/login.php"): [response(302, "", location="/index.php")],
            ("GET", "/info.php"): [response(200, LOGIN_HTML), response(200, INFO_HTML)],
            ("POST", "/info-async.php"): [response(200, INFO_JSON)],
        }
    )
    assert AmplifiClient(http, password="secret").fetch_info_async() == INFO_JSON


def test_login_that_does_not_stick_raises_login_error() -> None:
    http = FakeHttpClient(
        {
            ("GET", "/login.php"): [response(200, LOGIN_HTML)],
            ("POST", "/login.php"): [response(302, "", location="/index.php")],
            ("GET", "/info.php"): [response(302, "", location="/login.php")],
        }
    )
    with pytest.raises(LoginError):
        AmplifiClient(http, password="secret").fetch_info_async()


def test_expired_session_is_renewed_once_and_the_fetch_succeeds() -> None:
    http = FakeHttpClient(
        {
            ("GET", "/login.php"): [response(200, LOGIN_HTML)],
            ("POST", "/login.php"): [response(302, "", location="/index.php")],
            ("GET", "/info.php"): [
                response(200, INFO_HTML),
                response(302, "", location="/login.php"),
                response(200, INFO_HTML),
            ],
            ("POST", "/info-async.php"): [response(200, INFO_JSON)],
        }
    )
    client = AmplifiClient(http, password="secret")
    assert client.fetch_info_async() == INFO_JSON
    assert client.fetch_info_async() == INFO_JSON
    logins = [c for c in http.calls if c.method == "POST" and c.path == "/login.php"]
    assert len(logins) == 1
