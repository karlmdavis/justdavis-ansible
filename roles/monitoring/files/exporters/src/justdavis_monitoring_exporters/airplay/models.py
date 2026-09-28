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
class UnicastAnswer:
    """What came back from a unicast query: whether anything answered, and the fields of the TXT record
    when the answer carried one."""

    answered: bool
    txt: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class PlaybackState:
    """What a HomePod's `_airplay._tcp` TXT record says it is doing (see `airplay/state.py`)."""

    audio_playing: bool
    group_leader: bool
    group_id: str
    status_flags: int


@dataclass(frozen=True, slots=True)
class ServiceObservation:
    """What one poll found out about a service: whether an active multicast query resolved it, whether the
    passive browser currently lists it, when it was last seen any way (None if never), whether a
    unicast query sent straight to the device answered (None when its address is unknown, the query
    could not be made, or the service is not queried by unicast), and what the unicast answer's TXT
    record said the HomePod is doing (None when there was no answer or the record could not be read)."""

    resolved: bool
    discovered: bool
    last_seen: float | None
    unicast_resolved: bool | None = None
    state: PlaybackState | None = None


@dataclass(frozen=True, slots=True)
class AirplaySnapshot:
    services: Mapping[ServiceKey, ServiceObservation]
