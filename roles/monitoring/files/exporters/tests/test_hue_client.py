"""Tests for the Hue client: both APIs, and the application key kept out of every failure."""

import logging

import pytest
import requests
import urllib3

from justdavis_monitoring_exporters.common.errors import HttpStatusError, LoginError, ResponseTooLarge
from justdavis_monitoring_exporters.hue.client import FetchError, HueClient
from tests.fakes import FailingHttpClient, FakeHttpClient, response

KEY = "testkey-0123456789abcdef"


def client_with(http: FakeHttpClient) -> HueClient:
    return HueClient(http, application_key=KEY)


def test_fetches_both_apis_with_the_key_only_in_the_v1_path() -> None:
    http = FakeHttpClient(
        {
            ("GET", "/clip/v2/resource"): [response(200, b"{}")],
            ("GET", f"/api/{KEY}/config"): [response(200, b"{}")],
        }
    )
    client = client_with(http)
    assert client.fetch_resources() == b"{}"
    assert client.fetch_config() == b"{}"
    assert [c.path for c in http.calls] == ["/clip/v2/resource", f"/api/{KEY}/config"]
    client.close_connections()
    assert http.connection_closes == 1


def test_repr_omits_the_key() -> None:
    assert KEY not in repr(client_with(FakeHttpClient({})))


def _tls(message: str) -> requests.exceptions.SSLError:
    return requests.exceptions.SSLError(
        urllib3.exceptions.SSLError(f"HTTPSConnectionPool: /api/{KEY}/config ({message})")
    )


FAILURES: list[tuple[str, Exception, type[Exception], str | None]] = [
    ("403", HttpStatusError(403, f"/api/{KEY}/config"), LoginError, None),
    ("401", HttpStatusError(401, "/clip/v2/resource"), LoginError, None),
    ("429", HttpStatusError(429, "/clip/v2/resource"), FetchError, "fetch"),
    ("500", HttpStatusError(500, f"/api/{KEY}/config"), FetchError, "fetch"),
    ("chain", _tls("certificate verify failed: unable to get local issuer certificate"), FetchError, "tls"),
    ("name", _tls("hostname 'x' doesn't match 'y'"), FetchError, "tls"),
    ("tls cut", _tls("EOF occurred in violation of protocol"), FetchError, "fetch"),
    (
        "timeout",
        requests.exceptions.ConnectTimeout(f"https://192.0.2.21/api/{KEY}/config"),
        FetchError,
        "fetch",
    ),
    ("too large", ResponseTooLarge(f"/api/{KEY}/config exceeded 2097152 bytes"), FetchError, "fetch"),
    ("unexpected", RuntimeError(f"something about /api/{KEY}/config"), FetchError, "fetch"),
]


@pytest.mark.parametrize("fetch", ["fetch_resources", "fetch_config"])
@pytest.mark.parametrize(
    ("error", "expected", "stage"),
    [failure[1:] for failure in FAILURES],
    ids=[failure[0] for failure in FAILURES],
)
def test_every_failure_of_either_request_is_replaced_by_one_without_the_key(
    fetch: str,
    error: Exception,
    expected: type[Exception],
    stage: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    http = FailingHttpClient({("GET", "/clip/v2/resource"): [response(200, b"{}")]}, error=error)
    http.arm()
    client = client_with(http)
    with pytest.raises(expected) as raised:
        getattr(client, fetch)()
    # Nothing of the original survives: not in the message, the repr, the cause or the context.
    assert KEY not in str(raised.value)
    assert KEY not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    # And so not in what the scrape loop logs, which is the exception with its traceback.
    with caplog.at_level(logging.WARNING):
        logging.getLogger("test").warning("poll failed", exc_info=raised.value)
    assert "poll failed" in caplog.text
    assert KEY not in caplog.text
    if stage is not None:
        assert isinstance(raised.value, FetchError) and raised.value.stage == stage


def test_failure_messages_say_which_request_failed() -> None:
    http = FailingHttpClient({}, error=HttpStatusError(500, f"/api/{KEY}/config"))
    http.arm()
    client = client_with(http)
    with pytest.raises(FetchError, match=r"HTTP 500 from /api/<key>/config"):
        client.fetch_config()
    with pytest.raises(FetchError, match=r"HTTP 500 from /clip/v2/resource"):
        client.fetch_resources()


def test_certificate_problems_are_told_apart_by_exception_type_not_wording() -> None:
    import ssl

    from justdavis_monitoring_exporters.hue.client import _is_certificate_problem

    pool = urllib3.HTTPConnectionPool("example.invalid")
    chain = requests.exceptions.SSLError(
        urllib3.exceptions.MaxRetryError(
            pool, "/x", urllib3.exceptions.SSLError(ssl.SSLCertVerificationError("unusual wording"))
        )
    )
    assert _is_certificate_problem(chain) is True
    name = requests.exceptions.SSLError(urllib3.exceptions.SSLError(ssl.CertificateError("renamed message")))
    assert _is_certificate_problem(name) is True
    cut = requests.exceptions.SSLError(urllib3.exceptions.SSLError("EOF occurred in violation of protocol"))
    assert _is_certificate_problem(cut) is False
