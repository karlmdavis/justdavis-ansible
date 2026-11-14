"""HomeKit accessory presence from the `_hap._tcp` announcements the LAN discovery browser already hears.

Every HomeKit accessory on the LAN (the Hue bridge, HomePod sensors, plugs, cameras, thermostats)
advertises `_hap._tcp` with a TXT record that says who it is and how it is doing: `id` (its HomeKit
pairing identity), `md` (model), `ci` (category), `sf` (status flags: bit 0 set means NOT paired with
any controller, so a reset or dropped accessory shows up here), and `c#` (configuration number, bumped
when its services change). Read passively from the cache: nothing is asked of the accessories, so the
wired-origin multicast fault that loses 5 GHz devices' answers does not make this flap, and a
disappearance is noticed only when the record's TTL (75 minutes) runs out. An accessory's last-seen
time comes from its TXT record's arrival, and the accessory stays reported (as not present) until
`retention_seconds` after that, so the gauges end on their own for an accessory that is really gone.
Instance names from the network are sanitised and the set is capped, so a guest's gadget cannot flood
the label space.
"""

import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace

from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily, Metric
from prometheus_client.registry import Collector

from justdavis_monitoring_exporters.common.labels import sanitise_label
from justdavis_monitoring_exporters.common.snapshot import SnapshotHolder
from justdavis_monitoring_exporters.lan_discovery.mdns import HAP_TYPE, CachedInstance

log = logging.getLogger(__name__)
RETENTION_SECONDS = 48 * 3600
MAX_ACCESSORIES = 64
# HAP accessory categories (`ci`), as the HomeKit Accessory Protocol specification numbers them; an
# unlisted code is exported as the number.
CATEGORY_NAMES: dict[str, str] = {
    "1": "other",
    "2": "bridge",
    "3": "fan",
    "4": "garage_door_opener",
    "5": "lightbulb",
    "6": "door_lock",
    "7": "outlet",
    "8": "switch",
    "9": "thermostat",
    "10": "sensor",
    "11": "security_system",
    "12": "door",
    "13": "window",
    "14": "window_covering",
    "15": "programmable_switch",
    "16": "range_extender",
    "17": "ip_camera",
    "18": "video_doorbell",
    "19": "air_purifier",
    "20": "heater",
    "21": "air_conditioner",
    "22": "humidifier",
    "23": "dehumidifier",
    "24": "apple_tv",
    "25": "homepod",
    "26": "speaker",
    "27": "airport",
    "28": "sprinkler",
    "29": "faucet",
    "30": "shower_head",
    "31": "television",
    "32": "target_controller",
    "33": "router",
    "34": "audio_receiver",
    "35": "tv_set_top_box",
    "36": "tv_streaming_stick",
}
# Given an announced address, the name the device address inventory has for the device there (a tracked
# client's name, or the router's name for it, which is whatever it was called in the AmpliFi app); ""
# when nothing is known.
DeviceNamer = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class HapAccessory:
    """One accessory as last announced: the TXT fields that matter, and when and where."""

    instance: str
    identity: str
    model: str
    category: str
    paired: bool | None
    config_number: int | None
    last_seen: float
    addresses: tuple[str, ...]
    # Still in the cache (its record has not expired) at the last refresh.
    present: bool
    # What the device address inventory calls the device at the announced address, or "". The
    # announcement's own instance name is often a factory label ("HomePodSensor 12345 ABCD"), while the
    # inventory knows the tracked client's name, or the router's, which the AmpliFi app can set.
    device: str


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def accessory_from_instance(
    cached: CachedInstance, *, present: bool, device_for_address: DeviceNamer | None = None
) -> HapAccessory | None:
    """The accessory an `_hap._tcp` instance describes; None when it has no TXT record yet."""
    if cached.txt_seen_at is None:
        return None
    txt = cached.txt
    status_flags = _int_or_none(txt.get("sf"))
    addresses = tuple(address for address, _ in cached.addresses)
    device = ""
    if device_for_address is not None:
        device = next((name for name in map(device_for_address, addresses) if name), "")
    return HapAccessory(
        instance=cached.instance,
        identity=txt.get("id", ""),
        model=txt.get("md", ""),
        category=txt.get("ci", ""),
        paired=None if status_flags is None else not status_flags & 1,
        config_number=_int_or_none(txt.get("c#")),
        last_seen=cached.txt_seen_at,
        addresses=addresses,
        present=present,
        device=device,
    )


class HomekitTracker:
    """Keeps the accessories seen across refreshes, so a vanished one stays reported (as not present)
    until retention runs out, and publishes a snapshot per refresh."""

    def __init__(
        self, *, retention_seconds: float = RETENTION_SECONDS, max_accessories: int = MAX_ACCESSORIES
    ) -> None:
        self._retention = retention_seconds
        self._max = max_accessories
        self._known: dict[str, HapAccessory] = {}
        self.snapshots: SnapshotHolder[tuple[HapAccessory, ...]] = SnapshotHolder()
        self._warned_cap = False

    def refresh(
        self,
        instances: Iterable[CachedInstance],
        *,
        now: float,
        device_for_address: DeviceNamer | None = None,
    ) -> tuple[HapAccessory, ...]:
        seen: set[str] = set()
        for cached in instances:
            if cached.service != HAP_TYPE:
                continue
            accessory = accessory_from_instance(cached, present=True, device_for_address=device_for_address)
            if accessory is None:
                continue
            seen.add(accessory.instance)
            previous = self._known.get(accessory.instance)
            if previous is None:
                self._known[accessory.instance] = accessory
                continue
            # The name is kept across polls in which the inventory has no fresh address for the device
            # (its window is minutes, an announcement's hours), so the info series does not churn.
            device = accessory.device or previous.device
            if accessory.last_seen >= previous.last_seen:
                self._known[accessory.instance] = replace(accessory, device=device)
            else:
                self._known[accessory.instance] = replace(previous, present=True, device=device)
        for instance, accessory in list(self._known.items()):
            if now - accessory.last_seen > self._retention:
                del self._known[instance]
            elif instance not in seen and accessory.present:
                self._known[instance] = replace(accessory, present=False)
        if len(self._known) > self._max:
            # Vanished accessories go first, then the longest unheard: a flood of guest announcements
            # must not evict the household's own, present accessories.
            oldest = sorted(self._known.values(), key=lambda a: (a.present, a.last_seen))[
                : len(self._known) - self._max
            ]
            for accessory in oldest:
                del self._known[accessory.instance]
            if not self._warned_cap:
                log.warning(
                    "more than %d HomeKit accessories announced; dropped %d, vanished ones first, then "
                    "the longest unheard",
                    self._max,
                    len(oldest),
                )
                self._warned_cap = True
        else:
            self._warned_cap = False
        snapshot = tuple(sorted(self._known.values(), key=lambda a: a.instance))
        self.snapshots.set(snapshot)
        return snapshot


_LABELS = ["accessory"]


class HomekitCollector(Collector):
    """The accessory gauges. The label is `accessory` (the sanitised instance name), never `instance`,
    which Prometheus reserves for the scrape target."""

    def __init__(self, tracker: HomekitTracker) -> None:
        self._tracker = tracker

    def collect(self) -> Iterator[Metric]:
        accessories = self._tracker.snapshots.get()
        if accessories is None:
            return
        info = InfoMetricFamily(
            "homekit_accessory",
            "A HomeKit accessory announced on the LAN: its pairing identity, model and category (from "
            "the last announcement heard), and `device`, what the device address inventory calls the "
            "device at the announced address (a tracked client's name, or the router's name for it, "
            "which the AmpliFi app can set), or empty.",
            labels=_LABELS,
        )
        present = GaugeMetricFamily(
            "homekit_accessory_present",
            "1 while the accessory's announcement is in the mDNS cache (it lags a disappearance by up to "
            "the record's 75-minute TTL); 0 once it has expired, until retention ends the series.",
            labels=_LABELS,
        )
        paired = GaugeMetricFamily(
            "homekit_accessory_paired",
            "1 when the accessory reports itself paired with a controller (status flag bit 0 clear); 0 "
            "means no controller is paired with it: reset, dropped from the Home, or never added (from "
            "the last announcement heard). Absent when the announcement carries no readable status flag.",
            labels=_LABELS,
        )
        config = GaugeMetricFamily(
            "homekit_accessory_config_number",
            "The accessory's HomeKit configuration number (c#), which it bumps when its services change.",
            labels=_LABELS,
        )
        last_seen = GaugeMetricFamily(
            "homekit_accessory_last_seen_timestamp_seconds",
            "Unix time the accessory's announcement was last heard (from the mDNS cache).",
            labels=_LABELS,
        )
        address = InfoMetricFamily(
            "homekit_accessory_address", "The address the accessory announced.", labels=_LABELS
        )
        for accessory in accessories:
            labels = [sanitise_label(accessory.instance)]
            info.add_metric(
                labels,
                {
                    "id": sanitise_label(accessory.identity),
                    "model": sanitise_label(accessory.model),
                    "category": CATEGORY_NAMES.get(accessory.category, sanitise_label(accessory.category)),
                    "device": sanitise_label(accessory.device),
                },
            )
            present.add_metric(labels, float(accessory.present))
            if accessory.paired is not None:
                paired.add_metric(labels, float(accessory.paired))
            if accessory.config_number is not None:
                config.add_metric(labels, float(accessory.config_number))
            last_seen.add_metric(labels, accessory.last_seen)
            for addr in accessory.addresses:
                address.add_metric(labels, {"address": addr})
        yield info
        yield present
        yield paired
        yield config
        yield last_seen
        yield address
