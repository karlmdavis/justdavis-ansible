"""The AirPlay mDNS service types, in the one place that knows how they are spelled.

The same type appears in three forms: zeroconf's `"_airplay._tcp.local."`, the AmpliFi router's Bonjour
table `"_airplay._tcp.local"`, and the short `"_airplay._tcp"` used as a metric label. Everything else
works with the short form and converts at the edges.
"""

from typing import Literal, get_args

type AirplayService = Literal["_airplay._tcp", "_raop._tcp"]

AIRPLAY_SERVICES: tuple[AirplayService, ...] = get_args(AirplayService.__value__)


def mdns_type(service: AirplayService) -> str:
    """The fully qualified service type zeroconf uses: `"_airplay._tcp"` -> `"_airplay._tcp.local."`."""
    return f"{service}.local."


def short_service(service_type: str) -> str:
    """`"_airplay._tcp.local."` or `"_airplay._tcp.local"` -> `"_airplay._tcp"` (any service type)."""
    return service_type.removesuffix(".").removesuffix(".local")


def airplay_service(service_type: str) -> AirplayService:
    """Narrow a service type in any spelling to one of the AirPlay services, or raise `ValueError`."""
    short = short_service(service_type)
    for service in AIRPLAY_SERVICES:
        if short == service:
            return service
    raise ValueError(f"{service_type!r} is not an AirPlay service type")
