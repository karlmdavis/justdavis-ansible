"""Records for the AirPlay mDNS probe."""

from collections.abc import Mapping
from dataclasses import dataclass


def short_service(service_type: str) -> str:
    """`"_airplay._tcp.local."` -> `"_airplay._tcp"`."""
    return service_type.removesuffix(".").removesuffix(".local")


@dataclass(frozen=True, slots=True)
class ServiceKey:
    """An mDNS service instance identified by the device's AirPlay name and the short service type.

    RAOP instances carry a device-id prefix on the wire (`AABBCCDDEEFF@Kitchen`); `from_mdns` strips it
    so both AirPlay service types key on the same name.
    """

    name: str
    service: str

    @classmethod
    def from_mdns(cls, service_type: str, instance: str) -> "ServiceKey":
        name = instance.removesuffix("." + service_type).removesuffix("." + service_type.rstrip("."))
        if "@" in name:
            name = name.split("@", 1)[1]
        return cls(name, short_service(service_type))


@dataclass(frozen=True, slots=True)
class AirplaySnapshot:
    resolved: Mapping[ServiceKey, bool]
    discovered: frozenset[ServiceKey]
    last_seen: Mapping[ServiceKey, float]
