"""HTTP client for the Comcast Business gateway admin UI (Technicolor CGA4332COM, verified 2026-09).

Login is `POST /check.jst` with form fields `username` and `password`; success is a 302 to
`at_a_glance.jst` and a session cookie. A page requested without a valid session comes back as HTTP 200
with the login form or a logged-out script stub, so "needs login" is detected by content: anything
that is not the status page (`gateway.parser.is_status_page`) counts, which also covers a logged-out
shape this code has not seen yet.

The gateway allows a single admin session and writes a system-log entry on every login, so this client
keeps one session alive across polls and re-logs in only when something other than the status page
comes back. Re-logins are additionally rate limited (`relogin_min_seconds`) so that a person
using the GUI is not logged out every poll; a throttled attempt raises `LoginThrottled`, which the
exporter reports as `gateway_up 0` without counting a login error (the shared scrape loop still logs it
and backs off). The throttle counts from the attempt, not from success, so rejected credentials produce
one login error and then throttled attempts until the window passes, rather than a login-log flood.
"""

import logging
from collections.abc import Callable

from justdavis_monitoring_exporters.common.errors import ExporterError, LoginError
from justdavis_monitoring_exporters.common.http import HttpClient, HttpResponse
from justdavis_monitoring_exporters.gateway.parser import is_login_page, is_status_page

log = logging.getLogger(__name__)

_LOGIN_PATH = "/check.jst"
_STATUS_PATH = "/comcast_network.jst"
# A successful login redirects here (verified 2026-09; "/at_a_glance.jst").
_LOGIN_SUCCESS_LOCATION = "at_a_glance"


class LoginThrottled(ExporterError):
    """A re-login was needed but suppressed by the rate limit."""


def _needs_login(page: HttpResponse) -> bool:
    """The gateway answers a session-less request with the login form (200), a logged-out script stub
    (200), or, on some paths, a redirect. Anything that is not the status page counts, so that a
    logged-out shape not seen before gets a re-login rather than a parse error on every poll."""
    return page.status != 200 or is_login_page(page.body) or not is_status_page(page.body)


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

    def fetch_comcast_network(self) -> HttpResponse:
        """Return the status page response (headers included, for diagnosing a page that does not
        parse), logging in (subject to the throttle) when needed."""
        page = self._http.get(_STATUS_PATH)
        if not _needs_login(page):
            return page
        self._login()
        page = self._http.get(_STATUS_PATH)
        if _needs_login(page):
            raise LoginError("gateway still did not return the status page after logging in")
        log.info("logged in to the gateway")
        return page

    def close_connections(self) -> None:
        """Drop the kept-alive connection to the gateway; the session cookie is unaffected."""
        self._http.close_connections()

    def _login(self) -> None:
        now = self._clock()
        if (
            self._last_login_attempt is not None
            and now - self._last_login_attempt < self._relogin_min_seconds
        ):
            raise LoginThrottled("session lost; re-login suppressed by the rate limit")
        self._last_login_attempt = now
        result = self._http.post(_LOGIN_PATH, data={"username": self._username, "password": self._password})
        if 300 <= result.status < 400 and _LOGIN_SUCCESS_LOCATION in result.headers.get("location", ""):
            return
        if result.status == 200 and is_login_page(result.body):
            raise LoginError("gateway rejected the credentials")
        # Anything else is not a login this code understands; failing here names the login step rather
        # than leaving the next status fetch to fail with "still did not return the status page".
        raise LoginError(f"unexpected response to login: HTTP {result.status}")
