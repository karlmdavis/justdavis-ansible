"""Prometheus collector for the Hue bridge snapshot."""

from collections.abc import Iterator
from dataclasses import dataclass

from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily, Metric
from prometheus_client.registry import Collector

from justdavis_monitoring_exporters.common.labels import sanitise_label
from justdavis_monitoring_exporters.common.metrics import status_families
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder
from justdavis_monitoring_exporters.hue.models import HueSnapshot
from justdavis_monitoring_exporters.hue.parser import INTERNET_SERVICES

_DEVICE = ["device_id", "name"]
_LIGHT = ["light_id", "device_id", "name"]
# The bridge's Zigbee link states (2026-10-31), plus "unknown" for a value this code has not seen, so
# an unfamiliar state is visible rather than read as "not connected".
ZIGBEE_STATUSES: tuple[str, ...] = (
    "connected",
    "disconnected",
    "connectivity_issue",
    "unidirectional_incoming",
    "unknown",
)


@dataclass(frozen=True, slots=True)
class PublishedHue:
    snapshot: HueSnapshot
    # Where the bridge was reached this poll (from the device address inventory).
    address: str


class HueCollector(Collector):
    def __init__(self, statuses: SnapshotHolder[ScrapeStatus]) -> None:
        self.statuses = statuses
        self._published: SnapshotHolder[PublishedHue] = SnapshotHolder()

    def publish(self, snapshot: HueSnapshot, *, address: str) -> None:
        self._published.set(PublishedHue(snapshot, address))

    def clear(self) -> None:
        self._published.clear()

    def collect(self) -> Iterator[Metric]:
        yield from status_families("hue", self.statuses.get())
        published = self._published.get()
        if published is None:
            return
        yield from _bridge_families(published)
        yield from _device_families(published.snapshot)
        yield from _light_families(published.snapshot)
        yield from _sensor_families(published.snapshot)


def _bridge_families(published: PublishedHue) -> Iterator[Metric]:
    bridge = published.snapshot.bridge
    info = InfoMetricFamily("hue_bridge", "The bridge's identity and software, from its configuration.")
    info.add_metric(
        [],
        {
            "bridge_id": sanitise_label(bridge.bridge_id),
            "model": sanitise_label(bridge.model),
            "swversion": sanitise_label(bridge.swversion),
            "apiversion": sanitise_label(bridge.apiversion),
        },
    )
    yield info
    address = InfoMetricFamily(
        "hue_bridge_address",
        "The address the bridge was reached at this poll (from the device address inventory).",
        labels=["bridge_id"],
    )
    address.add_metric([sanitise_label(bridge.bridge_id)], {"address": published.address})
    yield address
    yield GaugeMetricFamily(
        "hue_bridge_zigbee_channel",
        "The Zigbee channel the bridge's lights and sensors are on (11, 15, 20 or 25).",
        value=float(bridge.zigbee_channel),
    )
    services = GaugeMetricFamily(
        "hue_bridge_internet_connected",
        "1 when the bridge reports the named cloud service (internet, remoteaccess, time, swupdate) as "
        "connected; matters for out-of-home control, not for HomeKit or the Hue app on the LAN.",
        labels=["service"],
    )
    for service in INTERNET_SERVICES:
        services.add_metric([service], float(bridge.internet_services.get(service, False)))
    yield services
    if bridge.last_install is not None:
        yield GaugeMetricFamily(
            "hue_bridge_last_software_install_timestamp_seconds",
            "Unix time the bridge last installed its own software; the bridge is unresponsive for a "
            "few minutes while it does.",
            value=bridge.last_install,
        )


def _device_families(snapshot: HueSnapshot) -> Iterator[Metric]:
    info = InfoMetricFamily(
        "hue_device", "Each device the bridge knows, with its product and room.", labels=_DEVICE
    )
    zigbee = GaugeMetricFamily(
        "hue_zigbee_connectivity",
        "One series per device and Zigbee link status (connected, disconnected, connectivity_issue, "
        "unidirectional_incoming, unknown); exactly one of them is 1. A lamp switched off at the wall "
        "reads disconnected.",
        labels=[*_DEVICE, "status"],
    )
    battery = GaugeMetricFamily(
        "hue_device_battery_ratio", "Battery charge of a battery-powered device, 0 to 1.", labels=_DEVICE
    )
    for device in snapshot.devices:
        labels = [device.device_id, sanitise_label(device.name)]
        info.add_metric(
            labels,
            {
                "product": sanitise_label(device.product),
                "model": sanitise_label(device.model),
                "software_version": sanitise_label(device.software_version),
                "room": sanitise_label(device.room),
            },
        )
        if device.zigbee_status is not None:
            current = device.zigbee_status if device.zigbee_status in ZIGBEE_STATUSES else "unknown"
            for status in ZIGBEE_STATUSES:
                zigbee.add_metric([*labels, status], float(status == current))
        if device.battery_ratio is not None:
            battery.add_metric(labels, device.battery_ratio)
    yield info
    yield zigbee
    yield battery


def _light_families(snapshot: HueSnapshot) -> Iterator[Metric]:
    on = GaugeMetricFamily("hue_light_on", "1 when the light (or plug) is on.", labels=_LIGHT)
    brightness = GaugeMetricFamily(
        "hue_light_brightness_ratio",
        "Brightness of a dimmable light, 0 to 1 (absent for a plug).",
        labels=_LIGHT,
    )
    for light in snapshot.lights:
        labels = [light.light_id, light.device_id, sanitise_label(light.name)]
        on.add_metric(labels, float(light.on))
        if light.brightness_ratio is not None:
            brightness.add_metric(labels, light.brightness_ratio)
    yield on
    yield brightness


def _sensor_families(snapshot: HueSnapshot) -> Iterator[Metric]:
    temperature = GaugeMetricFamily(
        "hue_sensor_temperature_celsius", "Temperature reported by a Hue sensor.", labels=_DEVICE
    )
    raw = GaugeMetricFamily(
        "hue_sensor_light_level_raw",
        "Light level as the bridge reports it: 10000 * log10(lux) + 1.",
        labels=_DEVICE,
    )
    lux = GaugeMetricFamily(
        "hue_sensor_light_level_lux", "Light level in lux, derived from the raw value.", labels=_DEVICE
    )
    motion = GaugeMetricFamily(
        "hue_sensor_motion", "1 while a Hue motion sensor reports motion.", labels=_DEVICE
    )
    for sensor in snapshot.sensors:
        labels = [sensor.device_id, sanitise_label(sensor.name)]
        if sensor.temperature_celsius is not None:
            temperature.add_metric(labels, sensor.temperature_celsius)
        if sensor.light_level_raw is not None:
            raw.add_metric(labels, float(sensor.light_level_raw))
            lux.add_metric(labels, 10 ** ((sensor.light_level_raw - 1) / 10000))
        if sensor.motion is not None:
            motion.add_metric(labels, float(sensor.motion))
    yield temperature
    yield raw
    yield lux
    yield motion
