"""HTTP client for the Comcast Business gateway admin UI (Technicolor CGA4332COM, verified 2026-09).

Login is `POST /check.jst` with form fields `username` and `password`; success is a 302 to
`at_a_glance.jst` and a session cookie. Any page requested without a valid session returns the login
form with HTTP 200, so "needs login" is detected by content (`gateway.parser.is_login_page`).

The gateway allows a single admin session and writes a system-log entry on every login, so this client
keeps one session alive across polls and re-logs in only when the login form comes back. Re-logins are
additionally rate limited (`relogin_min_seconds`) so that a person using the GUI is not logged out every
poll; a throttled attempt raises `LoginThrottled`, which the exporter reports as a quiet `gateway_up 0`.
"""

import logging
from collections.abc import Callable

from justdavis_monitoring_exporters.common.errors import ExporterError, LoginError
from justdavis_monitoring_exporters.common.http import HttpClient
from justdavis_monitoring_exporters.gateway.parser import is_login_page

log = logging.getLogger(__name__)

_LOGIN_PATH = "/check.jst"
_STATUS_PATH = "/comcast_network.jst"


class LoginThrottled(ExporterError):
    """A re-login was needed but suppressed by the rate limit."""


class GatewayClient:
    def __init__(
        self,
        http: HttpClient,
        *,
        username: str,
        password: str,
        relogin_min_seconds: float,
        clock: Callable[[], float],
    ) -> None:
        self._http = http
        self._username = username
        self._password = password
        self._relogin_min_seconds = relogin_min_seconds
        self._clock = clock
        self._last_login_attempt: float | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(http={self._http!r}, username={self._username!r})"

    def fetch_comcast_network(self) -> bytes:
        """Return the raw status page body, logging in (subject to the throttle) when needed."""
        page = self._http.get(_STATUS_PATH)
        if not is_login_page(page.body):
            return page.body
        self._login()
        page = self._http.get(_STATUS_PATH)
        if is_login_page(page.body):
            raise LoginError("gateway still shows the login form after logging in")
        return page.body

    def _login(self) -> None:
        now = self._clock()
        if (
            self._last_login_attempt is not None
            and now - self._last_login_attempt < self._relogin_min_seconds
        ):
            raise LoginThrottled("session lost; re-login suppressed by the rate limit")
        self._last_login_attempt = now
        result = self._http.post(_LOGIN_PATH, data={"username": self._username, "password": self._password})
        if result.status == 200 and is_login_page(result.body):
            raise LoginError("gateway rejected the credentials")
        log.info("logged in to the gateway")
