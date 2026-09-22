"""Environment-variable parsing shared by the exporters.

Every exporter builds a frozen settings object once at startup from `os.environ`; a malformed value
raises `SettingsError`, which the entry point turns into exit code 2 with a clear message. An EMPTY host
variable is not an error: it is the "not configured" state used in test environments, where the
exporter serves `<name>_up 0` and idles instead of failing every cycle.
"""

import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast, get_args

from justdavis_monitoring_exporters.common.errors import ExporterError
from justdavis_monitoring_exporters.common.jsonutil import as_dict, as_list, as_str

type ClientKind = Literal["homepod", "appletv", "ipad", "phone", "laptop", "other"]

_CLIENT_KINDS: tuple[str, ...] = get_args(ClientKind.__value__)
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class SettingsError(ExporterError):
    """A configuration value is malformed."""


def canonical_mac(value: str) -> str:
    """Lowercase, colon-separated form of a MAC address (no validation; see `normalise_mac`)."""
    return value.strip().lower().replace("-", ":")


def normalise_mac(value: str, ctx: str) -> str:
    mac = canonical_mac(value)
    if not _MAC_RE.match(mac):
        raise SettingsError(f"{ctx}.mac: {value!r} is not a MAC address")
    return mac


@dataclass(frozen=True, slots=True)
class TrackedClient:
    """A device the user cares about, giving stable labels to its metrics and ping/AirPlay probes."""

    mac: str
    name: str
    kind: ClientKind
    airplay_name: str

    def __post_init__(self) -> None:
        if not _MAC_RE.match(self.mac):
            raise SettingsError(f"TrackedClient.mac: {self.mac!r} is not a canonical MAC address")
        if not self.name:
            raise SettingsError("TrackedClient.name: must not be empty")


def _kind(value: str, ctx: str) -> ClientKind:
    if value in _CLIENT_KINDS:
        return cast(ClientKind, value)
    raise SettingsError(f"{ctx}.kind: {value!r} is not one of {', '.join(_CLIENT_KINDS)}")


def parse_tracked_clients(raw: str) -> tuple[TrackedClient, ...]:
    """Parse the `MONITORING_TRACKED_CLIENTS` JSON list; blank means no tracked clients."""
    if not raw.strip():
        return ()
    try:
        decoded: object = json.loads(raw)
        entries = as_list(decoded, "MONITORING_TRACKED_CLIENTS")
        clients: list[TrackedClient] = []
        for index, entry_value in enumerate(entries):
            ctx = f"MONITORING_TRACKED_CLIENTS[{index}]"
            entry = as_dict(entry_value, ctx)
            name = as_str(entry.get("name", ""), f"{ctx}.name")
            if not name:
                raise SettingsError(f"{ctx}.name: missing")
            airplay_name = as_str(entry.get("airplay_name", name), f"{ctx}.airplay_name")
            clients.append(
                TrackedClient(
                    mac=normalise_mac(as_str(entry.get("mac", ""), f"{ctx}.mac"), ctx),
                    name=name,
                    kind=_kind(as_str(entry.get("kind", ""), f"{ctx}.kind"), ctx),
                    airplay_name=airplay_name,
                )
            )
        seen: set[str] = set()
        for client in clients:
            if client.mac in seen:
                raise SettingsError(f"MONITORING_TRACKED_CLIENTS: duplicate mac {client.mac}")
            seen.add(client.mac)
    except (ValueError, ExporterError) as exc:
        raise SettingsError(f"MONITORING_TRACKED_CLIENTS: {exc}") from exc
    return tuple(clients)


def parse_static_targets(raw: str) -> tuple[str, ...]:
    """Parse the `MONITORING_STATIC_PING_TARGETS` JSON list of IPv4/IPv6 addresses (duplicates dropped)."""
    if not raw.strip():
        return ()
    try:
        decoded: object = json.loads(raw)
        targets: list[str] = []
        for index, value in enumerate(as_list(decoded, "MONITORING_STATIC_PING_TARGETS")):
            text = as_str(value, f"MONITORING_STATIC_PING_TARGETS[{index}]")
            address = str(ipaddress.ip_address(text))
            if address not in targets:
                targets.append(address)
    except (ValueError, ExporterError) as exc:
        raise SettingsError(f"MONITORING_STATIC_PING_TARGETS: {exc}") from exc
    return tuple(targets)


def parse_fingerprint(raw: str, name: str) -> str | None:
    """Validate a SHA-256 certificate fingerprint (hex, colons optional); blank means none."""
    text = raw.strip()
    if not text:
        return None
    if not _FINGERPRINT_RE.match(text.lower().replace(":", "")):
        raise SettingsError(f"{name}: not a SHA-256 fingerprint")
    return text


def env_str(env: Mapping[str, str], name: str, default: str) -> str:
    return env.get(name, default)


def env_int(
    env: Mapping[str, str], name: str, default: int, *, minimum: int | None = None, maximum: int | None = None
) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise SettingsError(f"{name}: {raw!r} is not an integer") from exc
    if minimum is not None and value < minimum:
        raise SettingsError(f"{name}: {value} is below the minimum of {minimum}")
    if maximum is not None and value > maximum:
        raise SettingsError(f"{name}: {value} is above the maximum of {maximum}")
    return value


def env_port(env: Mapping[str, str], name: str, default: int) -> int:
    return env_int(env, name, default, minimum=1, maximum=65535)


def env_host(env: Mapping[str, str], name: str) -> str | None:
    """Return the host, or None when the variable is absent or blank ("not configured")."""
    raw = env.get(name, "").strip()
    return raw or None
