"""The device address inventory: which LAN address each device (by MAC) has, from several sources.

Why it exists (2026-10-30): the ping and AirPlay probes used to learn addresses from one place, the
AmpliFi router's WiFi client table. When that scrape failed for a day the probe target files froze
while the HomePods' DHCP addresses moved, so the probes watched the wrong devices and reported a
HomePod's AirPlay service as gone. A wired device never appeared in that table at all.

How it works. Several PRODUCERS each write their own file under `/shared/device-addresses/`: the
router feed (`router.json`, written by the amplifi exporter), mDNS announcements (`mdns.json`) and the
kernel's neighbour table (`arp.json`, both written by the lan_discovery exporter). A producer never
reads another's file, so the latest data from every producer is preserved as it was observed. Within
its file a producer keeps every (device, address) pair it has seen, with first-seen and last-seen times,
bounded to the most recent `MAX_ADDRESSES_PER_MAC` addresses per device and to observations seen within
`RETENTION_SECONDS` (guest and rotating private MAC addresses would otherwise accumulate forever).

CONSUMERS collapse at time of use, through `AddressInventory.latest()`: per source the newest
observation within the caller's freshness window, then the first source in `SOURCES` order. ARP comes
first because a neighbour entry is the device itself answering for the address on this segment; the
router's directory is a lease list that can lag a reassignment; mDNS sits between. When no source has a
fresh observation the answer is None, and the caller must treat the device as having no known address
(no probe target, no gauge): that is the fail-safe this module exists for.

Every producer fills a small core (MAC, address, first/last seen) plus two optional fields: its own
`name` for the device and a `role` (only the router knows which entries are infrastructure). Anything
else a producer knows goes into `extra`, a string map stored raw and never read by this module; it is
for a person reading the files or a later consumer. Addresses are IPv4 only: every consumer (ping
targets, the AirPlay unicast socket, blackbox) speaks IPv4 on this LAN, and an IPv6 address would
break them.

File shape (`sort_keys` JSON, one document per source):

    {"source": "arp", "written_at": 1793400000.0, "observations": [
      {"mac": "02:00:00:00:00:10", "address": "192.0.2.110", "first_seen": 1793300000.0,
       "last_seen": 1793399990.0, "name": "Speaker-A", "role": "client", "extra": {"state": "REACHABLE"}}]}
"""

import ipaddress
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast, get_args

from justdavis_monitoring_exporters.common.files import write_if_changed
from justdavis_monitoring_exporters.common.jsonutil import as_dict, as_list, as_str, get
from justdavis_monitoring_exporters.common.problem_log import ProblemLog
from justdavis_monitoring_exporters.common.settings import Mac, normalise_mac

log = logging.getLogger(__name__)

# In precedence order for `latest()`: see the module docstring.
type Source = Literal["arp", "mdns", "router"]
SOURCES: tuple[Source, ...] = get_args(Source.__value__)
type Role = Literal["client", "router", "mesh_point"]
_ROLES: tuple[str, ...] = get_args(Role.__value__)

DEVICE_ADDRESSES_DIR = "device-addresses"
MAX_ADDRESSES_PER_MAC = 8
RETENTION_SECONDS = 7 * 24 * 3600


def source_file(directory: Path, source: Source) -> Path:
    return directory / f"{source}.json"


def valid_address(address: str) -> str | None:
    """The address in canonical form, or None when it is not an IPv4 address."""
    try:
        return str(ipaddress.IPv4Address(address))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Sighting:
    """What a producer hands in for one poll: this MAC had this address at this time."""

    mac: Mac
    address: str
    observed_at: float
    name: str = ""
    role: Role = "client"
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Observation:
    """A (device, address) pair as one source has recorded it, with its first and last sighting."""

    source: Source
    mac: Mac
    address: str
    first_seen: float
    last_seen: float
    name: str = ""
    role: Role = "client"
    extra: Mapping[str, str] = field(default_factory=dict)


def _observation_from_json(value: object, source: Source, ctx: str) -> Observation | None:
    """One stored observation, or None (logged at debug) when the entry is unusable."""
    try:
        entry = as_dict(value, ctx)
        mac = normalise_mac(as_str(get(entry, "mac", ctx), f"{ctx}.mac"), ctx)
        address = valid_address(as_str(get(entry, "address", ctx), f"{ctx}.address"))
        if address is None:
            log.debug("%s: not an IPv4 address; skipped", ctx)
            return None
        first_seen = float(as_number(get(entry, "first_seen", ctx), f"{ctx}.first_seen"))
        last_seen = float(as_number(get(entry, "last_seen", ctx), f"{ctx}.last_seen"))
        name = as_str(entry.get("name", ""), f"{ctx}.name")
        role_text = as_str(entry.get("role", "client"), f"{ctx}.role")
        role: Role = cast(Role, role_text) if role_text in _ROLES else "client"
        extra_raw = as_dict(entry.get("extra", {}), f"{ctx}.extra")
        extra = {k: as_str(v, f"{ctx}.extra.{k}") for k, v in extra_raw.items()}
    except Exception as exc:
        log.debug("%s: unusable observation skipped: %s", ctx, exc)
        return None
    return Observation(source, mac, address, first_seen, last_seen, name, role, extra)


def as_number(value: object, ctx: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{ctx}: expected a number")
    return float(value)


def _observation_to_json(observation: Observation) -> dict[str, object]:
    return {
        "mac": observation.mac,
        "address": observation.address,
        "first_seen": observation.first_seen,
        "last_seen": observation.last_seen,
        "name": observation.name,
        "role": observation.role,
        "extra": dict(sorted(observation.extra.items())),
    }


@dataclass(frozen=True, slots=True)
class SourceDocument:
    source: Source
    written_at: float | None
    observations: tuple[Observation, ...]


def read_source(path: Path, source: Source, problems: ProblemLog | None = None) -> SourceDocument:
    """The source's file, tolerantly: a missing file is an absent source (no problem), a corrupt one
    is reported once and read as empty, and unusable entries are skipped one by one."""
    key = str(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if problems is not None:
            problems.clear(key, f"{path} is readable again")
        return SourceDocument(source, None, ())
    try:
        decoded: object = json.loads(raw)
        document = as_dict(decoded, path.name)
        written_at = as_number(get(document, "written_at", path.name), f"{path.name}.written_at")
        entries = as_list(get(document, "observations", path.name), f"{path.name}.observations")
    except Exception as exc:
        if problems is not None:
            problems.problem(key, f"ignoring {path}: {type(exc).__name__}: {exc}")
        return SourceDocument(source, None, ())
    if problems is not None:
        problems.clear(key, f"{path} is readable again")
    observations = tuple(
        observation
        for index, entry in enumerate(entries)
        if (observation := _observation_from_json(entry, source, f"{path.name}.observations[{index}]"))
        is not None
    )
    return SourceDocument(source, written_at, observations)


def _trimmed(observations: Iterable[Observation], *, now: float) -> tuple[Observation, ...]:
    """Retention and the per-MAC cap, in a stable order: by MAC, newest first."""
    kept: dict[Mac, list[Observation]] = {}
    for observation in observations:
        if now - observation.last_seen > RETENTION_SECONDS:
            continue
        kept.setdefault(observation.mac, []).append(observation)
    result: list[Observation] = []
    for mac in sorted(kept):
        per_mac = sorted(kept[mac], key=lambda o: (-o.last_seen, o.address))
        result.extend(per_mac[:MAX_ADDRESSES_PER_MAC])
    return tuple(result)


class SourceStore:
    """One producer's file. `record()` merges a poll's sightings into it and rewrites it.

    The merge key is (mac, address): a repeat sighting moves `last_seen` (and refreshes name, role and
    extra); a new address for a MAC is a new entry alongside the old ones, which is how the history is
    kept. `written_at` is the producer's heartbeat and changes every poll, so the file is rewritten
    every poll; nothing watches these files, so that costs nothing.
    """

    def __init__(self, directory: Path, source: Source) -> None:
        self._path = source_file(directory, source)
        self._source = source

    @property
    def path(self) -> Path:
        return self._path

    def record(self, sightings: Iterable[Sighting], *, now: float) -> None:
        existing = {(o.mac, o.address): o for o in read_source(self._path, self._source).observations}
        for sighting in sightings:
            address = valid_address(sighting.address)
            if address is None:
                log.debug(
                    "%s: %r is not an IPv4 address; sighting of %s skipped",
                    self._source,
                    sighting.address,
                    sighting.mac,
                )
                continue
            key = (sighting.mac, address)
            previous = existing.get(key)
            existing[key] = Observation(
                source=self._source,
                mac=sighting.mac,
                address=address,
                first_seen=min(previous.first_seen, sighting.observed_at)
                if previous
                else sighting.observed_at,
                last_seen=max(previous.last_seen, sighting.observed_at) if previous else sighting.observed_at,
                name=sighting.name or (previous.name if previous else ""),
                role=sighting.role,
                extra=dict(sighting.extra),
            )
        self._path.parent.mkdir(exist_ok=True)
        document = {
            "source": self._source,
            "written_at": now,
            "observations": [_observation_to_json(o) for o in _trimmed(existing.values(), now=now)],
        }
        write_if_changed(self._path, (json.dumps(document, indent=1, sort_keys=True) + "\n").encode())


class AddressInventory:
    """Every source's observations, read once per poll, collapsed only when asked."""

    def __init__(self, documents: Iterable[SourceDocument]) -> None:
        by_source = {document.source: document for document in documents}
        self.written_at: Mapping[Source, float | None] = {
            source: (by_source[source].written_at if source in by_source else None) for source in SOURCES
        }
        self.observations: tuple[Observation, ...] = tuple(
            observation
            for source in SOURCES
            if source in by_source
            for observation in by_source[source].observations
        )
        self._by_mac: dict[Mac, list[Observation]] = {}
        for observation in self.observations:
            self._by_mac.setdefault(observation.mac, []).append(observation)

    @classmethod
    def load(cls, directory: Path, *, problems: ProblemLog | None = None) -> "AddressInventory":
        """Read the three known files by name (never a glob, so a leftover temporary file is never
        parsed); a missing directory is simply an empty inventory."""
        return cls(read_source(source_file(directory, source), source, problems) for source in SOURCES)

    def latest(self, mac: Mac, *, now: float, max_age: float) -> Observation | None:
        """The freshest observation within `max_age` from the highest-precedence source that has one;
        None when no source has seen the device's address recently enough."""
        for source in SOURCES:
            candidates = [
                o for o in self._by_mac.get(mac, ()) if o.source == source and now - o.last_seen <= max_age
            ]
            if candidates:
                return max(candidates, key=lambda o: o.last_seen)
        return None

    def age(self, mac: Mac, *, now: float) -> float | None:
        """Seconds since any source last saw the device, or None if none ever has."""
        observations = self._by_mac.get(mac)
        if not observations:
            return None
        return max(0.0, now - max(o.last_seen for o in observations))

    def role(self, mac: Mac) -> Role:
        """What the device is: `router` or `mesh_point` when any source says so (only the router feed
        knows), else `client`. Independent of which source supplies the address."""
        roles = {o.role for o in self._by_mac.get(mac, ())}
        for role in ("router", "mesh_point"):
            if role in roles:
                return cast(Role, role)
        return "client"

    def name(self, mac: Mac) -> str:
        """The most recently recorded non-empty name any source gave the device, or ""."""
        named = [o for o in self._by_mac.get(mac, ()) if o.name]
        return max(named, key=lambda o: o.last_seen).name if named else ""

    def infrastructure(self, *, now: float, max_age: float) -> tuple[tuple[Mac, Observation], ...]:
        """The fresh address of every router or mesh point, by MAC (sorted), with the address taken from
        whichever source is freshest even though only the router feed knows the role."""
        result: list[tuple[Mac, Observation]] = []
        for mac in sorted(self._by_mac):
            if self.role(mac) == "client":
                continue
            observation = self.latest(mac, now=now, max_age=max_age)
            if observation is not None:
                result.append((mac, observation))
        return tuple(result)
