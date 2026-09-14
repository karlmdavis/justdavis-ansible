"""HTTP client for the AmpliFi router's web UI (unofficial, verified September 2026, protocol 140).

Login flow:

1. `GET /login.php` and extract the 16-character token from the hidden `token` input.
2. `POST /login.php` with form fields `token` and `password`; success sets the `webui-session` cookie
   and redirects. A response that still contains the login form means the password was rejected.
3. `GET /info.php` and extract the 16-character token embedded in the page's script as `token='...'`.
4. `POST /info-async.php` with form fields `do=full` and `token`; the body is the JSON feed parsed by
   `amplifi.parser`.

The session expires after inactivity: `GET /info.php` then redirects to `/login.php` (or serves the
login form directly), which triggers a fresh login. The redirect is only used as a signal; its
`Location` is never fetched. The UI is plain HTTP, so the password crosses the LAN in cleartext on
each login. HTTP error statuses surface as `HttpStatusError` from the HTTP client.
"""

import logging
import re

from justdavis_monitoring_exporters.common.errors import LoginError
from justdavis_monitoring_exporters.common.http import HttpClient, HttpResponse

log = logging.getLogger(__name__)

_LOGIN_TOKEN_RE = re.compile(r"name=['\"]token['\"][^>]*value=['\"]([A-Za-z0-9]{16})['\"]")
_INFO_TOKEN_RE = re.compile(r"token=['\"]([A-Za-z0-9]{16})['\"]")
_LOGIN_PATH = "/login.php"
_INFO_PATH = "/info.php"
_INFO_ASYNC_PATH = "/info-async.php"


def _needs_login(response: HttpResponse) -> bool:
    location = response.headers.get("location", "")
    if 300 <= response.status < 400 and "login.php" in location:
        return True
    return _looks_like_login_form(response.body)


def _looks_like_login_form(body: bytes) -> bool:
    return _LOGIN_TOKEN_RE.search(body.decode("utf-8", errors="replace")) is not None


class AmplifiClient:
    def __init__(self, http: HttpClient, *, password: str) -> None:
        self._http = http
        self._password = password

    def __repr__(self) -> str:
        return f"{type(self).__name__}(http={self._http!r})"

    def fetch_info_async(self) -> bytes:
        """Return the raw `info-async.php` body, logging in first (or again) when needed."""
        info = self._http.get(_INFO_PATH)
        if _needs_login(info):
            self._login()
            info = self._http.get(_INFO_PATH)
            if _needs_login(info):
                raise LoginError("router still requires login after a successful-looking login")
            log.info("logged in to the AmpliFi router")
        token = self._extract(_INFO_TOKEN_RE, info.body, "info page token")
        return self._http.post(_INFO_ASYNC_PATH, data={"do": "full", "token": token}).body

    def _login(self) -> None:
        page = self._http.get(_LOGIN_PATH)
        token = self._extract(_LOGIN_TOKEN_RE, page.body, "login page token")
        result = self._http.post(_LOGIN_PATH, data={"token": token, "password": self._password})
        if result.status == 200 and _looks_like_login_form(result.body):
            raise LoginError("router rejected the password")

    @staticmethod
    def _extract(pattern: re.Pattern[str], body: bytes, what: str) -> str:
        match = pattern.search(body.decode("utf-8", errors="replace"))
        if match is None:
            raise LoginError(f"{what} not found (page format changed?)")
        return match.group(1)
