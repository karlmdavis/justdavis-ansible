"""Parsers for the Hue bridge's two APIs, as read from a BSB002 bridge on API 1.78 (2026-10-31).

* `GET /clip/v2/resource` (CLIP v2, header `hue-application-key`): `{"errors": [...], "data": [...]}`,
  every resource in one list, each with `id`, `type` and an `owner` (`{rid, rtype}`) pointing at the
  device it belongs to. Read here: `device` (identity, `product_data`, `metadata.name`, `services`),
  `light` (`on.on`, `dimming.brightness` 0-100), `zigbee_connectivity` (`status`),
  `device_power` (`power_state.battery_level` and `battery_state`), `temperature`, `light_level` and
  `motion` (each with a `*_report` sub-object, preferred over the older top-level fields), and `room`
  (`children` naming the devices in it). Everything else (scenes, zones, buttons, entertainment) is
  skipped. The CLIP v2 reference itself sits behind the Hue developer portal's login; the field names
  and enumerations here were checked (2026-11-08) against aiohue's models, the public transcription of
  that reference maintained for Home Assistant.
* `GET /api/<key>/config` (v1): `zigbeechannel`, `internetservices` (`internet`, `remoteaccess`,
  `time`, `swupdate`: "connected" or "disconnected"), `swupdate2.bridge.lastinstall`
  (`YYYY-MM-DDTHH:MM:SS`, UTC), and the public identity fields. With a key the bridge
  does not know it answers 200 with the PUBLIC subset of the same object (no `zigbeechannel`), so a
  rejected key is told from the shape, not the status; the v2 call runs first and does answer 403.

Values from the bridge never appear in error messages (names could be anything); the JSON path does.
This module is pure and never returns partial results: a block this exporter reads that has gone
missing, or a resource owned by no known device, is a parse error rather than a series that quietly
ends, because a renamed field after a bridge update must not read as "nothing is wrong".
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime

from justdavis_monitoring_exporters.common.errors import LoginError, ParseError
from justdavis_monitoring_exporters.common.jsonutil import (
    as_bool,
    as_dict,
    as_float,
    as_int,
    as_list,
    as_str,
    get,
)
from justdavis_monitoring_exporters.hue.models import BridgeConfig, HueDevice, HueLight, HueSensor

INTERNET_SERVICES: tuple[str, ...] = ("internet", "remoteaccess", "time", "swupdate")
# A rejected key gets the public configuration back: this field is in the full one only.
_AUTHENTICATED_ONLY_FIELD = "zigbeechannel"


def _optional_dict(mapping: Mapping[str, object], key: str, ctx: str) -> Mapping[str, object] | None:
    value = mapping.get(key)
    return None if value is None else as_dict(value, f"{ctx}.{key}")


def _timestamp(value: object, ctx: str) -> float | None:
    """The bridge's `YYYY-MM-DDTHH:MM:SS` (no zone; UTC) as epoch seconds; None for its placeholders."""
    text = as_str(value, ctx)
    if not text or text.lower() in ("none", "null"):
        return None
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()
    except ValueError as exc:
        raise ParseError(f"{ctx}: not a timestamp") from exc


def parse_config(body: bytes | str) -> BridgeConfig:
    """The v1 configuration; `LoginError` when the bridge answered with the public subset."""
    try:
        decoded: object = json.loads(body)
    except ValueError as exc:
        raise ParseError("config: not valid JSON") from exc
    config = as_dict(decoded, "config")
    if _AUTHENTICATED_ONLY_FIELD not in config:
        raise LoginError("the bridge did not accept the application key (public configuration returned)")
    services_raw = as_dict(get(config, "internetservices", "config"), "config.internetservices")
    services = {
        name: as_str(get(services_raw, name, "config.internetservices"), f"config.internetservices.{name}")
        == "connected"
        for name in INTERNET_SERVICES
    }
    last_install: float | None = None
    swupdate = _optional_dict(config, "swupdate2", "config")
    if swupdate is not None:
        bridge_update = _optional_dict(swupdate, "bridge", "config.swupdate2")
        if bridge_update is not None and "lastinstall" in bridge_update:
            last_install = _timestamp(bridge_update["lastinstall"], "config.swupdate2.bridge.lastinstall")
    return BridgeConfig(
        # Lowercase, as the certificate's common name and the configured id spell it.
        bridge_id=as_str(get(config, "bridgeid", "config"), "config.bridgeid").lower(),
        model=as_str(get(config, "modelid", "config"), "config.modelid"),
        swversion=as_str(get(config, "swversion", "config"), "config.swversion"),
        apiversion=as_str(get(config, "apiversion", "config"), "config.apiversion"),
        zigbee_channel=as_int(
            get(config, _AUTHENTICATED_ONLY_FIELD, "config"), f"config.{_AUTHENTICATED_ONLY_FIELD}"
        ),
        internet_services=services,
        last_install=last_install,
    )


def _owner_id(resource: Mapping[str, object], ctx: str) -> str:
    owner = as_dict(get(resource, "owner", ctx), f"{ctx}.owner")
    return as_str(get(owner, "rid", f"{ctx}.owner"), f"{ctx}.owner.rid")


def _reported(resource: Mapping[str, object], section: str, field: str, ctx: str) -> object | None:
    """`<section>.<field>_report.<field>` when present (the current API), else `<section>.<field>`; None
    when the bridge marks the reading invalid (`<field>_valid` false), the sensor is disabled, or it has
    no reading yet. The section itself must be there."""
    if resource.get("enabled") is False:
        return None
    block = as_dict(get(resource, section, ctx), f"{ctx}.{section}")
    if block.get(f"{field}_valid") is False:
        return None
    report = _optional_dict(block, f"{field}_report", f"{ctx}.{section}")
    if report is not None and field in report:
        return report[field]
    return block.get(field)


def parse_resources(
    body: bytes | str,
) -> tuple[tuple[HueDevice, ...], tuple[HueLight, ...], tuple[HueSensor, ...]]:
    """Every device, light and sensor in the bridge's resource list."""
    try:
        decoded: object = json.loads(body)
    except ValueError as exc:
        raise ParseError("resources: not valid JSON") from exc
    document = as_dict(decoded, "resources")
    errors = as_list(document.get("errors", []), "resources.errors")
    if errors:
        raise ParseError(
            f"resources.errors: the bridge reported {len(errors)} error(s) with the resource list"
        )
    by_type: dict[str, list[tuple[Mapping[str, object], str]]] = {}
    for index, value in enumerate(as_list(get(document, "data", "resources"), "resources.data")):
        ctx = f"resources.data[{index}]"
        resource = as_dict(value, ctx)
        by_type.setdefault(as_str(get(resource, "type", ctx), f"{ctx}.type"), []).append((resource, ctx))

    device_ids = {
        as_str(get(resource, "id", ctx), f"{ctx}.id") for resource, ctx in by_type.get("device", [])
    }

    def owner_of(resource: Mapping[str, object], ctx: str) -> str:
        """The owning device's id, which must be a device in this list: readings that no longer join to
        a device would otherwise vanish without a word."""
        owner = _owner_id(resource, ctx)
        if owner not in device_ids:
            raise ParseError(f"{ctx}.owner.rid: not a device in the resource list")
        return owner

    zigbee: dict[str, str] = {}
    for resource, ctx in by_type.get("zigbee_connectivity", []):
        zigbee[owner_of(resource, ctx)] = as_str(get(resource, "status", ctx), f"{ctx}.status")
    battery: dict[str, float] = {}
    battery_state: dict[str, str] = {}
    for resource, ctx in by_type.get("device_power", []):
        power = as_dict(get(resource, "power_state", ctx), f"{ctx}.power_state")
        owner = owner_of(resource, ctx)
        if power.get("battery_level") is not None:
            battery[owner] = as_float(power["battery_level"], f"{ctx}.power_state.battery_level") / 100
        if power.get("battery_state") is not None:
            battery_state[owner] = as_str(power["battery_state"], f"{ctx}.power_state.battery_state")
    rooms: dict[str, str] = {}
    for resource, ctx in by_type.get("room", []):
        name = as_str(
            get(as_dict(get(resource, "metadata", ctx), f"{ctx}.metadata"), "name", f"{ctx}.metadata"),
            f"{ctx}.metadata.name",
        )
        for child_index, child_value in enumerate(as_list(resource.get("children", []), f"{ctx}.children")):
            child = as_dict(child_value, f"{ctx}.children[{child_index}]")
            if as_str(child.get("rtype", ""), f"{ctx}.children[{child_index}].rtype") == "device":
                child_ctx = f"{ctx}.children[{child_index}]"
                rooms[as_str(get(child, "rid", child_ctx), f"{child_ctx}.rid")] = name
    temperature: dict[str, float] = {}
    for resource, ctx in by_type.get("temperature", []):
        value = _reported(resource, "temperature", "temperature", ctx)
        if value is not None:
            temperature[owner_of(resource, ctx)] = as_float(value, f"{ctx}.temperature.temperature")
    light_level: dict[str, int] = {}
    for resource, ctx in by_type.get("light_level", []):
        value = _reported(resource, "light", "light_level", ctx)
        if value is not None:
            light_level[owner_of(resource, ctx)] = as_int(value, f"{ctx}.light.light_level")
    motion: dict[str, bool] = {}
    for resource, ctx in by_type.get("motion", []):
        value = _reported(resource, "motion", "motion", ctx)
        if value is not None:
            motion[owner_of(resource, ctx)] = as_bool(value, f"{ctx}.motion.motion")

    devices: list[HueDevice] = []
    names: dict[str, str] = {}
    for resource, ctx in by_type.get("device", []):
        device_id = as_str(get(resource, "id", ctx), f"{ctx}.id")
        product = as_dict(get(resource, "product_data", ctx), f"{ctx}.product_data")
        metadata = as_dict(get(resource, "metadata", ctx), f"{ctx}.metadata")
        name = as_str(get(metadata, "name", f"{ctx}.metadata"), f"{ctx}.metadata.name")
        names[device_id] = name
        devices.append(
            HueDevice(
                device_id=device_id,
                name=name,
                product=as_str(product.get("product_name", ""), f"{ctx}.product_data.product_name"),
                model=as_str(product.get("model_id", ""), f"{ctx}.product_data.model_id"),
                software_version=as_str(
                    product.get("software_version", ""), f"{ctx}.product_data.software_version"
                ),
                room=rooms.get(device_id, ""),
                zigbee_status=zigbee.get(device_id),
                battery_ratio=battery.get(device_id),
                battery_state=battery_state.get(device_id),
            )
        )
    lights: list[HueLight] = []
    for resource, ctx in by_type.get("light", []):
        owner = owner_of(resource, ctx)
        on = as_dict(get(resource, "on", ctx), f"{ctx}.on")
        dimming = _optional_dict(resource, "dimming", ctx)
        brightness = None
        if dimming is not None and dimming.get("brightness") is not None:
            brightness = as_float(dimming["brightness"], f"{ctx}.dimming.brightness") / 100
        light_metadata = _optional_dict(resource, "metadata", ctx)
        light_name = as_str(light_metadata.get("name", ""), f"{ctx}.metadata.name") if light_metadata else ""
        lights.append(
            HueLight(
                light_id=as_str(get(resource, "id", ctx), f"{ctx}.id"),
                device_id=owner,
                name=light_name or names.get(owner, ""),
                on=as_bool(get(on, "on", f"{ctx}.on"), f"{ctx}.on.on"),
                brightness_ratio=brightness,
            )
        )
    sensors = tuple(
        HueSensor(
            device_id=device_id,
            name=names.get(device_id, ""),
            temperature_celsius=temperature.get(device_id),
            light_level_raw=light_level.get(device_id),
            motion=motion.get(device_id),
        )
        for device_id in sorted(set(temperature) | set(light_level) | set(motion))
    )
    return tuple(devices), tuple(lights), sensors
