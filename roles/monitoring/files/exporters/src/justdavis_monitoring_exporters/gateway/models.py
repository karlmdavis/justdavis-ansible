"""Parsed records for the Comcast Business gateway (Technicolor CGA4332COM)."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocsisChannel:
    """One downstream DOCSIS channel as shown on the gateway's "Comcast Network" page."""

    index: int
    locked: bool
    frequency_hz: int
    snr_db: float
    power_dbmv: float
    modulation: str
    unerrored: int
    correctable: int
    uncorrectable: int


@dataclass(frozen=True, slots=True)
class GatewayStatus:
    uptime_seconds: int
    internet_active: bool
    wan_ip: str
    wan_static_ip: str
    isp_gateway: str
    downstream: tuple[DocsisChannel, ...]
