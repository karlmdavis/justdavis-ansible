"""HTTP client for the Hue bridge's two APIs, with the application key kept out of every message.

The key is a credential: it travels as the `hue-application-key` header on CLIP v2 requests (set on the
session, so this module never sees it leave) and, for the one v1 request this exporter still needs, in
the URL path. Everything `requests` raises carries the URL in its message, and so does the shared
client's `HttpStatusError`, so every failure is re-raised here as a sanitised exception `from None`,
with the stage the exporter should count it under.
"""

import ssl

import requests

from justdavis_monitoring_exporters.common.errors import ExporterError, HttpStatusError, LoginError
from justdavis_monitoring_exporters.common.http import HttpClient
from justdavis_monitoring_exporters.common.metrics import ErrorStage

_RESOURCES_PATH = "/clip/v2/resource"
_CONFIG_PATH = "/api/{key}/config"
_CONFIG_PATH_REDACTED = "/api/<key>/config"
# urllib3's messages for a certificate that is not the bridge's: the chain (`certificate verify failed`)
# or the name (`doesn't match`), matched as a fallback to the exception types below. Anything else
# TLS-shaped is a connection problem.
_CERTIFICATE_PROBLEMS = ("certificate verify failed", "doesn't match", "CERTIFICATE_VERIFY_FAILED")


def _is_certificate_problem(exc: BaseException) -> bool:
    """Whether a TLS failure is about the certificate (chain or name), found by type in the chain of
    wrapped exceptions requests builds (`SSLError(MaxRetryError(SSLError(CertificateError)))`), with
    urllib3's wording as the fallback."""
    seen: set[int] = set()
    queue: list[BaseException] = [exc]
    while queue:
        current = queue.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError | ssl.CertificateError):
            return True
        if type(current).__name__ == "CertificateError":
            return True
        queue.extend(arg for arg in current.args if isinstance(arg, BaseException))
        # urllib3's MaxRetryError carries the underlying error as `reason`, not in its args.
        reason = getattr(current, "reason", None)
        if isinstance(reason, BaseException):
            queue.append(reason)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                queue.append(linked)
    return any(problem in str(exc) for problem in _CERTIFICATE_PROBLEMS)


class FetchError(ExporterError):
    """A request to the bridge failed; `stage` says which step of the poll to count it under."""

    def __init__(self, stage: ErrorStage, message: str) -> None:
        super().__init__(message)
        self.stage = stage


class HueClient:
    def __init__(self, http: HttpClient, *, application_key: str) -> None:
        self._http = http
        self._key = application_key

    def __repr__(self) -> str:
        return f"{type(self).__name__}(http={self._http!r})"

    def fetch_resources(self) -> bytes:
        """The CLIP v2 resource list (every device, light, sensor, room); 403 means the key is wrong."""
        return self._get(_RESOURCES_PATH, redacted=_RESOURCES_PATH)

    def fetch_config(self) -> bytes:
        """The v1 configuration (Zigbee channel, clock, cloud links, last software install)."""
        return self._get(_CONFIG_PATH.format(key=self._key), redacted=_CONFIG_PATH_REDACTED)

    def close_connections(self) -> None:
        self._http.close_connections()

    def _get(self, path: str, *, redacted: str) -> bytes:
        try:
            return self._http.get(path).body
        except requests.exceptions.SSLError as exc:
            if _is_certificate_problem(exc):
                raise FetchError(
                    "tls", f"the certificate of the bridge at {redacted} is not the bridge's"
                ) from None
            raise FetchError("fetch", "TLS connection failure") from None
        except (requests.RequestException, OSError) as exc:
            raise FetchError("fetch", f"request failed: {type(exc).__name__}") from None
        except HttpStatusError as exc:
            if exc.status in (401, 403):
                raise LoginError("the bridge rejected the application key") from None
            if exc.status == 429:
                raise FetchError("fetch", "the bridge is throttling requests") from None
            raise FetchError("fetch", f"HTTP {exc.status} from {redacted}") from None
