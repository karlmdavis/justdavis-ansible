"""A small, hardened HTTP client used to talk to the router and gateway.

All requests carry a timeout and never follow redirects (the AmpliFi client only uses a redirect as a
"session expired" signal and never fetches its `Location`). Bodies are streamed with a size cap so a
hung or misbehaving device cannot block the scrape thread forever or exhaust memory, and 4xx/5xx
statuses raise `HttpStatusError` so an outage is reported as such rather than as a parse failure. The
`HttpClient` protocol lets tests drive the device clients with scripted responses.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter

from justdavis_monitoring_exporters.common.errors import HttpStatusError, ResponseTooLarge

_CHUNK = 65536
DEFAULT_TIMEOUT: tuple[float, float] = (5.0, 15.0)
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024


class CaseInsensitiveHeaders(Mapping[str, str]):
    def __init__(self, headers: Mapping[str, str]) -> None:
        self._items = {key.lower(): value for key, value in headers.items()}

    def __getitem__(self, key: str) -> str:
        return self._items[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"CaseInsensitiveHeaders({self._items!r})"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: CaseInsensitiveHeaders
    body: bytes


class HttpClient(Protocol):
    """The contract the device clients rely on: a returned response always has a status below 400
    (4xx/5xx raise `HttpStatusError`), redirects are never followed (3xx responses are returned as
    they are), and bodies are complete but capped (`ResponseTooLarge` otherwise)."""

    def get(self, path: str) -> HttpResponse: ...

    def post(self, path: str, *, data: Mapping[str, str]) -> HttpResponse: ...


class _RawResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    def iter_content(self, chunk_size: int) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class _Session(Protocol):
    def request(self, method: str, url: str, **kwargs: object) -> _RawResponse: ...


class RequestsHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        session: _Session | requests.Session | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        verify: bool | str = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = cast(_Session, session if session is not None else requests.Session())
        self._timeout = timeout
        self._max_body_bytes = max_body_bytes
        self._verify = verify

    def get(self, path: str) -> HttpResponse:
        return self._request("GET", path)

    def post(self, path: str, *, data: Mapping[str, str]) -> HttpResponse:
        return self._request("POST", path, data=data)

    def _request(self, method: str, path: str, **kwargs: object) -> HttpResponse:
        url = urljoin(self._base_url + "/", path.lstrip("/"))
        raw = self._session.request(
            method,
            url,
            timeout=self._timeout,
            allow_redirects=False,
            stream=True,
            verify=self._verify,
            **kwargs,
        )
        try:
            body = self._read_capped(raw)
        finally:
            raw.close()
        if raw.status_code >= 400:
            raise HttpStatusError(raw.status_code, path)
        return HttpResponse(status=raw.status_code, headers=CaseInsensitiveHeaders(raw.headers), body=body)

    def _read_capped(self, raw: _RawResponse) -> bytes:
        chunks: list[bytes] = []
        size = 0
        for chunk in raw.iter_content(_CHUNK):
            size += len(chunk)
            if size > self._max_body_bytes:
                raise ResponseTooLarge(f"response exceeded {self._max_body_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)


class FingerprintAdapter(HTTPAdapter):
    """HTTPS adapter that pins the server certificate by SHA-256 fingerprint.

    Used for the gateway, whose certificate (Comcast's, for `myrouter.io`) does not name its address:
    hostname verification cannot pass, and the same certificate and key ship on every gateway of the
    model, so pinning the exact certificate is as good as it gets.
    """

    def __init__(self, fingerprint: str) -> None:
        # Must be set before super().__init__(), which calls init_poolmanager().
        self.fingerprint = fingerprint
        super().__init__()

    def init_poolmanager(self, *args: object, **kwargs: object) -> None:
        kwargs["assert_fingerprint"] = self.fingerprint
        super().init_poolmanager(*args, **kwargs)  # type: ignore[arg-type]


def pinned_session(fingerprint_sha256: str) -> requests.Session:
    """A `requests.Session` whose HTTPS connections must present the pinned certificate."""
    session = requests.Session()
    # A proxy from the environment would get its own, unpinned connection pool; the gateway is on
    # the LAN, so never consult HTTPS_PROXY and friends.
    session.trust_env = False
    session.mount("https://", FingerprintAdapter(fingerprint_sha256))
    return session
