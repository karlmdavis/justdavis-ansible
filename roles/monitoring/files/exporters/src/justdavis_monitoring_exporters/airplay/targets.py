"""Where the AirPlay probe learns each HomePod's address, for its unicast queries.

The AmpliFi exporter writes `airplay-targets.json` into the shared directory: Prometheus `file_sd` JSON
for the TCP probe, one entry per HomePod currently on the WiFi, shaped
`{"targets": ["<ip>:7000"], "labels": {"name": "<tracked name>", "kind": "homepod"}}`
(`amplifi/targets.py` renders it). This module reads it back. The `name` label is the tracked client's
name, not its AirPlay name, and that is how `poll()` keys the addresses.

The file is advisory: when it is missing or malformed the poll carries on without unicast queries (the
gauge is simply absent for every HomePod) and the problem is logged once, not every poll.
"""

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from justdavis_monitoring_exporters.common.errors import ParseError
from justdavis_monitoring_exporters.common.jsonutil import as_dict, as_list, as_str, get

log = logging.getLogger(__name__)


def parse_airplay_targets(content: bytes) -> dict[str, str]:
    """Tracked name -> address from the file_sd JSON; `ParseError` when the shape is not the one above."""
    try:
        decoded: object = json.loads(content)
    except ValueError as exc:
        raise ParseError(f"airplay-targets: {exc}") from exc
    addresses: dict[str, str] = {}
    for index, entry_value in enumerate(as_list(decoded, "airplay-targets")):
        ctx = f"airplay-targets[{index}]"
        entry = as_dict(entry_value, ctx)
        labels = as_dict(get(entry, "labels", ctx), f"{ctx}.labels")
        name = as_str(get(labels, "name", f"{ctx}.labels"), f"{ctx}.labels.name")
        targets = as_list(get(entry, "targets", ctx), f"{ctx}.targets")
        if len(targets) != 1:
            raise ParseError(f"{ctx}.targets: expected one host:port, got {len(targets)}")
        host, separator, port = as_str(targets[0], f"{ctx}.targets[0]").rpartition(":")
        if not separator or not host or not port.isdigit():
            raise ParseError(f"{ctx}.targets[0]: expected host:port, got {targets[0]!r}")
        addresses[name] = host.strip("[]")
    return addresses


class AddressBook:
    """Reads the addresses before each poll, logging a problem once until it changes or clears."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._problem: str | None = None

    def read(self) -> Mapping[str, str]:
        try:
            addresses = parse_airplay_targets(self._path.read_bytes())
        except (OSError, ParseError) as exc:
            problem = f"{type(exc).__name__}: {exc}"
            if problem != self._problem:
                log.warning(
                    "polling without unicast queries: cannot read HomePod addresses from %s (%s)",
                    self._path,
                    exc,
                )
                self._problem = problem
            return {}
        if self._problem is not None:
            log.info("HomePod addresses are readable again from %s", self._path)
            self._problem = None
        return addresses
