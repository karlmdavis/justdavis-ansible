"""Records for the AirPlay mDNS probe."""

from collections.abc import Mapping
from dataclasses import dataclass

from justdavis_monitoring_exporters.common.mdns import AirplayService, airplay_service


@dataclass(frozen=True, slots=True)
class ServiceKey:
    """An mDNS service instance identified by the device's AirPlay name and the short service type.

    RAOP instances carry a device-id prefix on the wire (`AABBCCDDEEFF@Kitchen`); `from_mdns` strips it
    so both AirPlay service types key on the same name.
    """

    name: str
    service: AirplayService

    @classmethod
    def from_mdns(cls, service_type: str, instance: str) -> "ServiceKey":
        name = instance.removesuffix("." + service_type).removesuffix("." + service_type.rstrip("."))
        if "@" in name:
            name = name.split("@", 1)[1]
        return cls(name, airplay_service(service_type))


@dataclass(frozen=True, slots=True)
class ServiceObservation:
    """What one poll found out about a service: whether an active query resolved it, whether the passive
    browser currently lists it, and when it was last seen either way (None if never)."""

    resolved: bool
    discovered: bool
    last_seen: float | None


@dataclass(frozen=True, slots=True)
class AirplaySnapshot:
    services: Mapping[ServiceKey, ServiceObservation]
