"""Entry point: `lan-discovery-exporter`.

Passive discovery of the LAN's devices and their addresses, and the rendering of the probe target
files from it. Each poll: record what mDNS announcements and the kernel's neighbour table say into the
device address inventory (`common/device_addresses.py`), load the inventory (every source, the router
feed included), render `ping-targets.yml` and `airplay-targets.json` from the fresh addresses, publish the
targets and each tracked device's freshness, and for tracked devices with no fresh address ask the
network about them (one mDNS request, one datagram to each known address) so the next poll can see
them again.

`lan_discovery_up` is 1 when the inventory loaded and both target files were written this poll. A
source that fails is counted under its stage (`resolve` for mDNS, `read` for the neighbour table,
`arp` for the provoking) and logged, but does not fail the poll: the other sources still render.
Without a LAN address (the test environment) neither source runs and the files hold the static
targets only.
"""

import ipaddress
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from prometheus_client import CollectorRegistry, Counter

from justdavis_monitoring_exporters.common.device_addresses import (
    DEVICE_ADDRESSES_DIR,
    AddressInventory,
    Sighting,
    SourceStore,
)
from justdavis_monitoring_exporters.common.files import write_if_changed
from justdavis_monitoring_exporters.common.loop import start_scrape_thread
from justdavis_monitoring_exporters.common.metrics import ErrorStage, count_error, error_counter
from justdavis_monitoring_exporters.common.problem_log import ProblemLog
from justdavis_monitoring_exporters.common.server import (
    configure_logging,
    load_settings,
    run_until_stopped,
    serve,
)
from justdavis_monitoring_exporters.common.settings import (
    SettingsError,
    TrackedClient,
    env_host,
    env_int,
    env_port,
    env_str,
    parse_static_targets,
    parse_tracked_clients,
)
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder
from justdavis_monitoring_exporters.lan_discovery.collector import (
    LanDiscoveryCollector,
    LanDiscoverySnapshot,
    TrackedAddressState,
)
from justdavis_monitoring_exporters.lan_discovery.mdns import (
    AddressBrowser,
    CachedInstance,
    instances_by_hostname,
    mdns_sightings,
)
from justdavis_monitoring_exporters.lan_discovery.neigh import (
    Neighbour,
    lan_interface_index,
    neighbour_sightings,
    provoke,
    read_neighbours,
)
from justdavis_monitoring_exporters.lan_discovery.targets import (
    AIRPLAY_TARGETS_FILE,
    PING_TARGETS_FILE,
    PingConfig,
    render_airplay_targets,
    render_ping_targets,
    target_infos,
)

log = logging.getLogger(__name__)

_BACKOFF_CAP_SECONDS = 300
# How long to wait for a device to answer a targeted mDNS request.
_REQUEST_TIMEOUT_MS = 3000


@dataclass(frozen=True, slots=True)
class LanDiscoverySettings:
    # None is the "not configured" state (the test environment): no mDNS, no neighbour table, and the
    # target files hold the static targets only.
    lan_ip: str | None
    # None disables the neighbour-table source; only addresses inside the subnet count.
    lan_subnet: ipaddress.IPv4Network | None
    port: int
    listen_addr: str
    interval_seconds: int
    max_age_seconds: int
    tracked_clients: tuple[TrackedClient, ...]
    static_ping_targets: tuple[str, ...]
    shared_dir: Path
    ping: PingConfig

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "LanDiscoverySettings":
        subnet_text = env_str(env, "MONITORING_LAN_SUBNET", "").strip()
        subnet: ipaddress.IPv4Network | None = None
        if subnet_text:
            try:
                subnet = ipaddress.IPv4Network(subnet_text)
            except ValueError as exc:
                raise SettingsError(f"MONITORING_LAN_SUBNET: {exc}") from exc
        return cls(
            lan_ip=env_host(env, "MONITORING_LAN_IP"),
            lan_subnet=subnet,
            port=env_port(env, "MONITORING_LAN_DISCOVERY_PORT", 9804),
            listen_addr=env_str(env, "MONITORING_LISTEN_ADDR", "0.0.0.0"),
            interval_seconds=env_int(env, "MONITORING_LAN_DISCOVERY_INTERVAL", 30, minimum=1),
            max_age_seconds=env_int(env, "MONITORING_DEVICE_ADDRESS_MAX_AGE", 180, minimum=1),
            tracked_clients=parse_tracked_clients(env.get("MONITORING_TRACKED_CLIENTS", "")),
            static_ping_targets=parse_static_targets(env.get("MONITORING_STATIC_PING_TARGETS", "")),
            shared_dir=Path(env_str(env, "MONITORING_SHARED_DIR", "/shared")),
            ping=PingConfig(
                interval_seconds=env_int(env, "MONITORING_PING_INTERVAL", 1, minimum=1),
                timeout_seconds=env_int(env, "MONITORING_PING_TIMEOUT", 2, minimum=1),
                history_size=env_int(env, "MONITORING_PING_HISTORY_SIZE", 60, minimum=1),
            ),
        )


def build_registry() -> tuple[CollectorRegistry, LanDiscoveryCollector, Counter]:
    """The registry served over HTTP, with the collector and the per-stage error counter on it."""
    registry = CollectorRegistry()
    statuses: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    statuses.set(ScrapeStatus.initial())
    collector = LanDiscoveryCollector(statuses)
    registry.register(collector)
    errors = error_counter("lan_discovery", ("resolve", "read", "arp", "write"), registry)
    return registry, collector, errors


class MdnsSource(Protocol):
    """What the scraper needs from the mDNS browser (so a test can stand in for zeroconf)."""

    def cached_instances(self, *, now: float) -> tuple[CachedInstance, ...]: ...

    def request(self, fqdn: str, timeout_ms: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class NeighbourSource:
    """The neighbour-table source's fixed parameters, when it is enabled."""

    ifindex: int
    subnet: ipaddress.IPv4Network


class LanDiscoveryScraper:
    """One poll, as the module docstring describes it."""

    def __init__(
        self,
        settings: LanDiscoverySettings,
        collector: LanDiscoveryCollector,
        errors: Counter,
        *,
        mdns: MdnsSource | None,
        neighbours: NeighbourSource | None,
        read_neighbour_table: Callable[[], tuple[Neighbour, ...]] = read_neighbours,
    ) -> None:
        self._settings = settings
        self._collector = collector
        self._errors = errors
        self._mdns = mdns
        self._neighbours = neighbours
        self._read_neighbours = read_neighbour_table
        inventory_dir = settings.shared_dir / DEVICE_ADDRESSES_DIR
        self._inventory_dir = inventory_dir
        self._mdns_store = SourceStore(inventory_dir, "mdns")
        self._arp_store = SourceStore(inventory_dir, "arp")
        self._problems = ProblemLog(log)

    def __call__(self) -> None:
        now = time.time()
        instances = self._record_mdns(now)
        self._record_neighbours(now)
        try:
            inventory = AddressInventory.load(self._inventory_dir, problems=self._problems)
        except Exception:
            self._collector.clear()
            raise
        settings = self._settings
        infos = target_infos(
            settings.static_ping_targets,
            inventory,
            settings.tracked_clients,
            now=now,
            max_age=settings.max_age_seconds,
        )
        states = tuple(self._assess(inventory, client, now) for client in settings.tracked_clients)
        enabled = frozenset(
            source
            for source, on in (("mdns", self._mdns is not None), ("arp", self._neighbours is not None))
            if on
        )
        self._collector.publish(LanDiscoverySnapshot(infos, states, inventory.written_at, enabled))
        try:
            if write_if_changed(
                settings.shared_dir / PING_TARGETS_FILE, render_ping_targets(settings.ping, infos)
            ):
                log.info("rewrote %s with %d targets", PING_TARGETS_FILE, len(infos))
            if write_if_changed(settings.shared_dir / AIRPLAY_TARGETS_FILE, render_airplay_targets(infos)):
                log.info("rewrote %s", AIRPLAY_TARGETS_FILE)
        except OSError:
            count_error(self._errors, "write")
            self._collector.set_target_files_ok(False)
            raise
        self._collector.set_target_files_ok(True)
        self._nudge_stale_devices(inventory, states, instances)

    def _record_mdns(self, now: float) -> tuple[CachedInstance, ...]:
        if self._mdns is None:
            return ()
        try:
            instances = self._mdns.cached_instances(now=now)
            sightings = mdns_sightings(instances)
        except Exception:
            self._fail("resolve", "could not read the mDNS cache")
            return ()
        self._store(self._mdns_store, sightings, now)
        return instances

    def _record_neighbours(self, now: float) -> None:
        if self._neighbours is None:
            return
        try:
            neighbours = self._read_neighbours()
            sightings = neighbour_sightings(
                neighbours, ifindex=self._neighbours.ifindex, subnet=self._neighbours.subnet, now=now
            )
        except Exception:
            self._fail("read", "could not read the neighbour table")
            return
        self._store(self._arp_store, sightings, now)

    def _store(self, store: SourceStore, sightings: tuple[Sighting, ...], now: float) -> None:
        """A source file that cannot be written is a `write` failure of this exporter, not of the source."""
        try:
            store.record(sightings, now=now)
        except OSError:
            self._fail("write", f"could not write {store.path}")

    def _assess(self, inventory: AddressInventory, client: TrackedClient, now: float) -> TrackedAddressState:
        latest = inventory.latest(client.mac, now=now, max_age=self._settings.max_age_seconds)
        if latest is not None:
            return TrackedAddressState(client, max(0.0, now - latest.last_seen), True, latest.source)
        age = inventory.age(client.mac, now=now)
        freshest = max(inventory.observations_of(client.mac), key=lambda o: o.last_seen, default=None)
        return TrackedAddressState(client, age, False, freshest.source if freshest is not None else "")

    def _nudge_stale_devices(
        self,
        inventory: AddressInventory,
        states: tuple[TrackedAddressState, ...],
        instances: tuple[CachedInstance, ...],
    ) -> None:
        """For each tracked device with no fresh address: ask for its mDNS records (if it has ever been
        seen announcing) and send a datagram to each address any source has recorded for it."""
        stale = [state.client for state in states if not state.fresh]
        if not stale or self._settings.lan_ip is None:
            return
        known = instances_by_hostname(instances)
        if self._mdns is not None:
            for client in stale:
                cached = next((known[h] for h in inventory.hostnames(client.mac) if h in known), None)
                if cached is None:
                    continue
                try:
                    self._mdns.request(cached.fqdn, _REQUEST_TIMEOUT_MS)
                except Exception:
                    self._fail("resolve", f"could not request the mDNS records of {cached.fqdn}")
        if self._neighbours is not None:
            addresses = sorted({o.address for client in stale for o in inventory.observations_of(client.mac)})
            if addresses:
                try:
                    sent = provoke(self._settings.lan_ip, addresses)
                    log.debug("provoked %d addresses of %d stale devices", sent, len(stale))
                except Exception:
                    self._fail("arp", "could not provoke the stale devices' addresses")

    def _fail(self, stage: ErrorStage, what: str) -> None:
        count_error(self._errors, stage)
        log.warning("%s", what, exc_info=True)


def main() -> None:
    env = os.environ
    configure_logging(env)
    settings = load_settings(LanDiscoverySettings.from_env, env)
    registry, collector, errors = build_registry()
    stop = threading.Event()
    shutdown = serve(registry, addr=settings.listen_addr, port=settings.port)
    mdns: AddressBrowser | None = None
    neighbours: NeighbourSource | None = None
    if settings.lan_ip is None:
        log.warning("MONITORING_LAN_IP is not set; rendering the static targets only")
    else:
        # Both of these can be transient at boot (the LAN address not yet configured), so exit and
        # let the container's restart policy retry rather than idling until someone notices. zeroconf
        # silently binds nothing for an address that is not on a local interface, hence the check.
        ifindex = lan_interface_index(settings.lan_ip)
        if ifindex is None:
            log.error("MONITORING_LAN_IP %s is not an address of this host; exiting", settings.lan_ip)
            shutdown()
            sys.exit(1)
        try:
            mdns = AddressBrowser(settings.lan_ip)
        except Exception:
            log.error("could not start mDNS on %s; exiting", settings.lan_ip, exc_info=True)
            shutdown()
            sys.exit(1)
        if settings.lan_subnet is None:
            log.warning("MONITORING_LAN_SUBNET is not set; not reading the neighbour table")
        else:
            neighbours = NeighbourSource(ifindex, settings.lan_subnet)
    scraper = LanDiscoveryScraper(settings, collector, errors, mdns=mdns, neighbours=neighbours)
    thread = start_scrape_thread(
        "lan-discovery-scrape",
        scraper,
        interval_seconds=settings.interval_seconds,
        backoff_cap_seconds=_BACKOFF_CAP_SECONDS,
        stop=stop,
        status=collector.statuses,
    )
    run_until_stopped(
        scrape_thread=thread,
        stop=stop,
        shutdown_server=shutdown,
        cleanup=mdns.close if mdns is not None else None,
    )
