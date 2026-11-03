"""Parsed records for the Philips Hue bridge (model BSB002, API 1.78, read 2026-10-31)."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    """From the v1 `config` resource: identity, Zigbee channel, clock, cloud links, last update."""

    bridge_id: str
    model: str
    swversion: str
    apiversion: str
    zigbee_channel: int
    # The bridge's clock, as epoch seconds; None when the field is missing or unreadable.
    utc: float | None
    # internet, remoteaccess, time, swupdate: True when the bridge reports "connected".
    internet_services: Mapping[str, bool]
    # When the bridge last installed its own software, as epoch seconds (None if never or unknown).
    last_install: float | None


@dataclass(frozen=True, slots=True)
class HueDevice:
    """One device the bridge knows (a lamp, a plug, a sensor, a switch, the bridge itself)."""

    device_id: str
    name: str
    product: str
    model: str
    software_version: str
    # The room the device is in, or "" (zones are ignored: a device can be in several).
    room: str
    # The Zigbee link's status as the bridge reports it (connected, disconnected, connectivity_issue,
    # unidirectional_incoming, or a value this code has not seen); None for the bridge itself.
    zigbee_status: str | None
    zigbee_mac: str | None
    # Battery charge as a ratio 0..1 for battery-powered devices, else None.
    battery_ratio: float | None


@dataclass(frozen=True, slots=True)
class HueLight:
    light_id: str
    device_id: str
    name: str
    on: bool
    # Brightness as a ratio 0..1; None for a plug, which cannot dim.
    brightness_ratio: float | None


@dataclass(frozen=True, slots=True)
class HueSensor:
    """The environmental readings of a device that has any (the Hue motion sensor has all three)."""

    device_id: str
    name: str
    temperature_celsius: float | None
    # The bridge's log-scale light level (10000 * log10(lux) + 1); the collector derives lux.
    light_level_raw: int | None
    motion: bool | None


@dataclass(frozen=True, slots=True)
class HueSnapshot:
    bridge: BridgeConfig
    devices: tuple[HueDevice, ...]
    lights: tuple[HueLight, ...]
    sensors: tuple[HueSensor, ...]
