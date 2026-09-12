"""A small, hardened HTTP client used to talk to the router and gateway.

All requests carry a timeout, never follow redirects (callers inspect 3xx responses themselves and only
accept on-host `Location`s), and stream the body with a size cap so a hung or misbehaving device
cannot block the scrape thread forever or exhaust memory. The `HttpClient` protocol lets tests drive the
device clients with scripted responses.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urljoin, urlsplit

import requests

from justdavis_monitoring_exporters.common.errors import ResponseTooLarge

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


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpClient(Protocol):
    def get(self, path: str, *, params: Mapping[str, str] | None = None) -> HttpResponse: ...

    def post(self, path: str, *, data: Mapping[str, str]) -> HttpResponse: ...


def same_host(base_url: str, location: str) -> bool:
    """True when `location` (relative or absolute) stays on `base_url`'s scheme and host."""
    base = urlsplit(base_url)
    target = urlsplit(urljoin(base_url, location))
    return (target.scheme, target.hostname) == (base.scheme, base.hostname)


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
        session: _Session | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        verify: bool | str = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session: _Session = session if session is not None else cast(_Session, requests.Session())
        self._timeout = timeout
        self._max_body_bytes = max_body_bytes
        self._verify = verify

    @property
    def base_url(self) -> str:
        return self._base_url

    def get(self, path: str, *, params: Mapping[str, str] | None = None) -> HttpResponse:
        return self._request("GET", path, params=params)

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
