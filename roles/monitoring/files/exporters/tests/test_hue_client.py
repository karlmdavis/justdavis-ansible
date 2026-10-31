"""Tests for the Hue client: both APIs, and the application key kept out of every failure."""

import pytest
import requests
import urllib3

from justdavis_monitoring_exporters.common.errors import HttpStatusError, LoginError
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


@pytest.mark.parametrize(
    ("error", "expected", "stage"),
    [
        (HttpStatusError(403, f"/api/{KEY}/config"), LoginError, None),
        (HttpStatusError(401, "/clip/v2/resource"), LoginError, None),
        (HttpStatusError(429, "/clip/v2/resource"), FetchError, "fetch"),
        (HttpStatusError(500, f"/api/{KEY}/config"), FetchError, "fetch"),
        (_tls("certificate verify failed: unable to get local issuer certificate"), FetchError, "tls"),
        (_tls("hostname 'x' doesn't match 'y'"), FetchError, "tls"),
        (_tls("EOF occurred in violation of protocol"), FetchError, "fetch"),
        (requests.exceptions.ConnectTimeout(f"https://192.0.2.21/api/{KEY}/config"), FetchError, "fetch"),
    ],
    ids=["403", "401", "429", "500", "chain", "name", "tls cut", "timeout"],
)
def test_every_failure_is_reraised_without_the_key(
    error: Exception, expected: type[Exception], stage: str | None
) -> None:
    http = FailingHttpClient({("GET", "/clip/v2/resource"): [response(200, b"{}")]}, error=error)
    http.arm()
    client = client_with(http)
    with pytest.raises(expected) as raised:
        client.fetch_config()
    assert KEY not in str(raised.value)
    assert raised.value.__cause__ is None
    assert (
        raised.value.__context__ is None
        or KEY not in str(raised.value.__context__)
        or raised.value.__suppress_context__
    )
    if stage is not None:
        assert isinstance(raised.value, FetchError) and raised.value.stage == stage


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
