"""Records for the AirPlay mDNS probe."""

from collections.abc import Mapping, Set
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServiceKey:
    """An mDNS service instance: the device's Bonjour name and the short service type."""

    name: str
    service: str


@dataclass(frozen=True, slots=True)
class AirplaySnapshot:
    resolved: Mapping[ServiceKey, bool]
    discovered: Set[ServiceKey]
    last_seen: Mapping[ServiceKey, float]
