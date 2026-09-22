"""Exception types shared by all exporters.

`SettingsError` (common/settings.py) and `LoginThrottled` (gateway/client.py) also derive from
`ExporterError` and live next to the code that raises them.
"""


class ExporterError(Exception):
    """Base class for errors raised by the exporters' own code."""


class ParseError(ExporterError):
    """A device response did not have the expected structure.

    The message names the context (e.g. the JSON path or page section) so that a firmware change is
    easy to locate from the log line alone. The offending content itself is never included, because
    device pages can contain secrets.
    """


class LoginError(ExporterError):
    """Authentication against a device failed or was refused."""


class ResponseTooLarge(ExporterError):
    """A device response exceeded the configured body size cap."""


class HttpStatusError(ExporterError):
    """A device answered with an HTTP error status (4xx/5xx)."""

    def __init__(self, status: int, path: str) -> None:
        super().__init__(f"HTTP {status} from {path}")
        self.status = status
        self.path = path
