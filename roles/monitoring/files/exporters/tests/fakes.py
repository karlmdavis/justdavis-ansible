"""Test doubles shared across client tests: a scripted HTTP client and a fixed clock."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from justdavis_monitoring_exporters.common.http import CaseInsensitiveHeaders, HttpResponse


@dataclass
class Call:
    method: str
    path: str
    data: Mapping[str, str] | None = None


def response(status: int = 200, body: bytes | str = b"", **headers: str) -> HttpResponse:
    raw = body.encode() if isinstance(body, str) else body
    return HttpResponse(
        status=status,
        headers=CaseInsensitiveHeaders({k.replace("_", "-"): v for k, v in headers.items()}),
        body=raw,
    )


@dataclass
class FakeHttpClient:
    """Serves responses from a script keyed by `(METHOD, path)`; each key is a FIFO of responses.

    The last response for a key is repeated if the script runs dry, so a test can express "every
    later fetch returns X" without enumerating calls.
    """

    script: dict[tuple[str, str], list[HttpResponse]]
    calls: list[Call] = field(default_factory=list)

    def get(self, path: str) -> HttpResponse:
        self.calls.append(Call("GET", path))
        return self._next("GET", path)

    def post(self, path: str, *, data: Mapping[str, str]) -> HttpResponse:
        self.calls.append(Call("POST", path, data=data))
        return self._next("POST", path)

    def _next(self, method: str, path: str) -> HttpResponse:
        queue = self.script[(method, path)]
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FailingHttpClient(FakeHttpClient):
    """Serves the script until `fail_after` calls, then raises `error` from every request."""

    def __init__(self, script: dict[tuple[str, str], list[HttpResponse]], *, error: Exception) -> None:
        super().__init__(script)
        self.error: Exception | None = None
        self._armed = error

    def arm(self) -> None:
        self.error = self._armed

    def _next(self, method: str, path: str) -> HttpResponse:
        if self.error is not None:
            raise self.error
        return super()._next(method, path)
