"""Parsed records for the Comcast Business gateway (Technicolor CGA4332COM)."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocsisChannel:
    """One downstream DOCSIS channel as shown on the gateway's "Comcast Network" page.

    Numeric fields are `None` when the page shows a placeholder (blank, `----`, `N/A`), which happens
    for channels that are not locked; the channel is still reported so its lock state is visible.
    """

    index: int
    locked: bool
    frequency_hz: int | None
    snr_db: float | None
    power_dbmv: float | None
    modulation: str
    unerrored: int | None
    correctable: int | None
    uncorrectable: int | None


@dataclass(frozen=True, slots=True)
class GatewayStatus:
    uptime_seconds: int
    internet_active: bool
    wan_ip: str
    wan_static_ip: str
    isp_gateway: str
    downstream: tuple[DocsisChannel, ...]
