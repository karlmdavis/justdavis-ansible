"""Tests for the hardened HTTP adapter.

The adapter wraps `requests.Session`; these tests drive it with a fake session so no network is used.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from justdavis_monitoring_exporters.common.errors import HttpStatusError, ResponseTooLarge
from justdavis_monitoring_exporters.common.http import (
    CaseInsensitiveHeaders,
    FingerprintAdapter,
    RequestsHttpClient,
    lan_session,
    pinned_session,
)


@dataclass
class FakeRawResponse:
    status_code: int
    headers: dict[str, str]
    chunks: list[bytes]
    closed: bool = False
    # Raised after the chunks, as a connection cut mid-body would be.
    error: Exception | None = None

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        yield from self.chunks
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeSession:
    responses: list[FakeRawResponse]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeRawResponse:  # noqa: ANN401
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)

    def close(self) -> None:
        self.calls.append({"method": "close"})


def test_get_sets_timeout_disables_redirects_and_streams() -> None:
    session = FakeSession([FakeRawResponse(200, {"Content-Type": "text/html"}, [b"ok"])])
    client = RequestsHttpClient("http://10.1.10.1", session=session, timeout=(3.0, 10.0), max_body_bytes=1024)
    response = client.get("/comcast_network.jst")
    assert response.status == 200
    assert response.body == b"ok"
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "http://10.1.10.1/comcast_network.jst"
    assert call["timeout"] == (3.0, 10.0)
    assert call["allow_redirects"] is False
    assert call["stream"] is True


def test_post_sends_form_data() -> None:
    session = FakeSession([FakeRawResponse(302, {"Location": "/at_a_glance.jst"}, [])])
    client = RequestsHttpClient("http://10.1.10.1", session=session)
    response = client.post("/check.jst", data={"username": "u", "password": "p"})
    assert response.status == 302
    assert response.headers["location"] == "/at_a_glance.jst"
    assert session.calls[0]["data"] == {"username": "u", "password": "p"}


def test_body_over_cap_raises_and_closes_response() -> None:
    raw = FakeRawResponse(200, {}, [b"x" * 600, b"y" * 600])
    session = FakeSession([raw])
    client = RequestsHttpClient("http://10.1.10.1", session=session, max_body_bytes=1000)
    with pytest.raises(ResponseTooLarge):
        client.get("/big")
    assert raw.closed is True


def test_error_while_streaming_the_body_propagates_and_closes_response() -> None:
    raw = FakeRawResponse(200, {}, [b"partial"], error=ConnectionError("connection reset"))
    client = RequestsHttpClient("http://10.1.10.1", session=FakeSession([raw]))
    with pytest.raises(ConnectionError):
        client.get("/comcast_network.jst")
    assert raw.closed is True


def test_response_headers_are_case_insensitive() -> None:
    session = FakeSession([FakeRawResponse(200, {"Set-Cookie": "a=b"}, [b""])])
    client = RequestsHttpClient("http://10.1.10.1", session=session)
    response = client.get("/")
    assert response.headers["set-cookie"] == "a=b"
    assert response.headers.get("SET-COOKIE") == "a=b"


def test_verify_option_is_passed_through() -> None:
    session = FakeSession([FakeRawResponse(200, {}, [b""])])
    client = RequestsHttpClient("https://10.1.10.1", session=session, verify="/secrets/gateway_ca.pem")
    client.get("/")
    assert session.calls[0]["verify"] == "/secrets/gateway_ca.pem"


def test_fingerprint_pinning_mounts_an_https_adapter_that_asserts_the_fingerprint() -> None:
    session = pinned_session("ab:cd:ef")
    adapter = session.get_adapter("https://10.1.10.1/")
    assert isinstance(adapter, FingerprintAdapter)
    assert adapter.poolmanager.connection_pool_kw["assert_fingerprint"] == "ab:cd:ef"


def test_pinned_session_ignores_proxy_settings_from_the_environment() -> None:
    # requests would route through HTTPS_PROXY on a separate, unpinned pool.
    assert pinned_session("ab:cd:ef").trust_env is False


def test_lan_session_ignores_proxy_settings_from_the_environment() -> None:
    assert lan_session().trust_env is False


def test_error_status_raises_http_status_error() -> None:
    session = FakeSession([FakeRawResponse(503, {}, [b"busy"])])
    client = RequestsHttpClient("http://10.1.10.1", session=session)
    with pytest.raises(HttpStatusError) as excinfo:
        client.get("/info.php")
    assert excinfo.value.status == 503
    assert "/info.php" in str(excinfo.value)


def test_redirect_status_is_returned_not_raised() -> None:
    session = FakeSession([FakeRawResponse(302, {"Location": "/x"}, [])])
    assert RequestsHttpClient("http://10.1.10.1", session=session).get("/").status == 302


def test_response_headers_are_the_case_insensitive_type() -> None:
    session = FakeSession([FakeRawResponse(200, {"A": "b"}, [b""])])
    response = RequestsHttpClient("http://10.1.10.1", session=session).get("/")
    assert isinstance(response.headers, CaseInsensitiveHeaders)
