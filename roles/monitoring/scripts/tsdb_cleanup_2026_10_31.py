# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""One-off Prometheus cleanup after PR #163 deploys: delete the series that told a false story while the
AmpliFi exporter was down (2026-10-30 to 2026-11-01). The router rebooted, two HomePods swapped DHCP
addresses, and the exporter crashed on every poll from then on, so the probe target files it writes
froze with each of the two names on the other's address: the AirPlay port probe, the unicast mDNS probe
and `monitoring_target_info` for each name were about the other device, and `HomePodAirPlayServiceGone`
fired for a HomePod that was fine. The ping series are keyed by address and stayed true; so did
`AmpliFiScrapeFailing`. Both are kept.

The two HomePods' tracked names, AirPlay names and CORRECT (post-fix) addresses are given on the command
line, so the household's details stay out of the repository (the probe and `monitoring_target_info`
series carry the tracked name, the unicast gauge and the alert the AirPlay name, as in the vault). Every
bound is read from Prometheus: the window opens at the first failing `amplifi_up` sample (the script
refuses to run if the lookback holds more than that one outage) and closes, per series, at the first
sample after it that is true again (the probe on the right address, the info series with the right
pairing, the unicast gauge at 1).

Same procedure as tsdb_cleanup_2026_09_23.py: admin API on only for the deletion, SSH port forward,
dry run by default (the dry run needs no admin API).

    ssh -N -L 19090:127.0.0.1:9090 eddings.justdavis.com &
    uv run roles/monitoring/scripts/tsdb_cleanup_2026_10_31.py \\
        --swapped "Speaker-A=192.0.2.11" --airplay-name "Speaker-A=Speaker A" \\
        --swapped "Speaker-B=192.0.2.12" --airplay-name "Speaker-B=Speaker B"      # prints the plan
    scp roles/monitoring/scripts/prometheus-admin-api.yml eddings.justdavis.com:/tmp/
    ssh eddings.justdavis.com 'cd /opt/monitoring &&
        sudo docker compose -f docker-compose.yml -f /tmp/prometheus-admin-api.yml up -d prometheus'
    uv run roles/monitoring/scripts/tsdb_cleanup_2026_10_31.py \\
        --swapped "Speaker-A=192.0.2.11" --airplay-name "Speaker-A=Speaker A" \\
        --swapped "Speaker-B=192.0.2.12" --airplay-name "Speaker-B=Speaker B" --yes # deletes
    ssh eddings.justdavis.com 'cd /opt/monitoring && sudo docker compose up -d prometheus'
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass

# Wide enough to reach the start of the outage for a week after the fix.
LOOKBACK = "7d"
ALERTS = '__name__=~"ALERTS|ALERTS_FOR_STATE"'

type Params = dict[str, str | list[str]]


@dataclass(frozen=True, slots=True)
class Deletion:
    """One false story: the series to delete and the window of samples to delete them from."""

    why: str
    matchers: tuple[str, ...]
    # Unix times, inclusive.
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class HomePod:
    """A tracked HomePod as the vault describes it: `name` labels the probe and target-info series,
    `airplay_name` (its Bonjour name) the airplay exporter's series and the alert."""

    name: str
    airplay_name: str
    address: str


class Prometheus:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def _call(self, path: str, params: Params | None = None, *, method: str = "GET") -> dict[str, object]:
        """Call the API and return its JSON object (an empty dict for a bodiless 204)."""
        url = f"{self._base_url}{path}"
        data: bytes | None = None
        if params:
            encoded = urllib.parse.urlencode(params, doseq=True)
            if method == "GET":
                url = f"{url}?{encoded}"
            else:
                data = encoded.encode()
        request = urllib.request.Request(url, data=data, method=method)
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
        if not body:
            return {}
        parsed: object = json.loads(body)
        if not isinstance(parsed, dict):
            raise TypeError(f"{path}: expected a JSON object, got {type(parsed).__name__}")
        return parsed

    def _data(self, path: str, params: Params | None = None) -> dict[str, object]:
        data = self._call(path, params)["data"]
        if not isinstance(data, dict):
            raise TypeError(f"{path}: expected a JSON object under 'data'")
        return data

    def admin_api_enabled(self) -> bool:
        return self._data("/api/v1/status/flags").get("web.enable-admin-api") == "true"

    def scalar(self, query: str) -> float | None:
        """The single value of an instant query, or None when it returns no series."""
        result = self._data("/api/v1/query", {"query": query})["result"]
        if not isinstance(result, list):
            raise TypeError("/api/v1/query: expected a list under 'data.result'")
        values: list[float] = []
        for series in result:
            if not isinstance(series, dict):
                raise TypeError("/api/v1/query: expected a JSON object per series")
            _, value = series["value"]
            values.append(float(value))
        if len(values) > 1:
            raise ValueError(f"{query!r} matched {len(values)} series; expected one")
        return values[0] if values else None

    def first_sample_time(self, expr: str, *, after: float | None = None) -> float | None:
        """Unix time of the earliest sample matching `expr` in the lookback window, optionally only
        counting samples after `after`."""
        if after is not None:
            expr = f"({expr} and timestamp({expr}) > {after:.3f})"
        return self.scalar(f"min_over_time(timestamp({expr})[{LOOKBACK}:15s])")

    def delete_series(self, matchers: Sequence[str], start: float, end: float) -> None:
        params: Params = {"match[]": list(matchers), "start": f"{start:.3f}", "end": f"{end:.3f}"}
        self._call("/api/v1/admin/tsdb/delete_series", params, method="POST")

    def clean_tombstones(self) -> None:
        self._call("/api/v1/admin/tsdb/clean_tombstones", method="POST")


def utc(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(timestamp))


def bound(prometheus: Prometheus, label: str, expr: str, *, after: float | None = None) -> float:
    """The first time `expr` was true (after `after`), or exit: a missing bound means the fix is not
    deployed yet (or the window has passed), and guessing would delete the wrong samples."""
    found = prometheus.first_sample_time(expr, after=after)
    if found is None:
        sys.exit(f"could not determine the {label} bound from {expr!r}; refusing to guess")
    return found


def outage_start(prometheus: Prometheus) -> float:
    """When the AmpliFi exporter started failing: the swap happened at the router reboot a minute
    earlier, when the last target-file write was still correct, so a minute of true samples goes too."""
    changes = prometheus.scalar(f"changes(amplifi_up[{LOOKBACK}])")
    if changes != 2:
        sys.exit(
            f"amplifi_up changed {changes} times in the last {LOOKBACK}, not the 2 of one outage; refusing"
        )
    return bound(prometheus, "outage start", "amplifi_up == 0")


def plan(prometheus: Prometheus, a: HomePod, b: HomePod) -> list[Deletion]:
    start = outage_start(prometheus)
    deletions: list[Deletion] = []
    for mine, other in ((a, b), (b, a)):
        probe = f'{{job="airplay_tcp", name="{mine.name}", instance="{other.address}:7000"}}'
        probe_ok = bound(
            prometheus,
            f"{mine.name} probe on its own address",
            f'probe_success{{job="airplay_tcp", name="{mine.name}", instance="{mine.address}:7000"}}',
            after=start,
        )
        deletions.append(
            Deletion(
                f"the AirPlay port probe for {mine.name} pointed at {other.name}'s address",
                (probe,),
                start,
                probe_ok - 1,
            )
        )
        info = f'{{__name__="monitoring_target_info", name="{mine.name}", ip="{other.address}"}}'
        info_ok = bound(
            prometheus,
            f"{mine.name} target info on its own address",
            f'monitoring_target_info{{name="{mine.name}", ip="{mine.address}"}}',
            after=start,
        )
        deletions.append(
            Deletion(
                f"monitoring_target_info joined {mine.name}'s name to {other.name}'s address",
                (info,),
                start,
                info_ok - 1,
            )
        )
        unicast = f'airplay_service_unicast_resolved{{name="{mine.airplay_name}"}}'
        unicast_ok = bound(prometheus, f"{mine.name} unicast resolve", f"{unicast} == 1", after=start)
        deletions.append(
            Deletion(
                f"the unicast mDNS probe for {mine.name} was sent to {other.name}, and the alert it fed",
                (
                    f'{{__name__="airplay_service_unicast_resolved", name="{mine.airplay_name}"}}',
                    f'{{{ALERTS}, alertname="HomePodAirPlayServiceGone", name="{mine.airplay_name}"}}',
                ),
                start,
                unicast_ok - 1,
            )
        )
    return deletions


def pair(value: str) -> tuple[str, str]:
    left, separator, right = value.partition("=")
    if not separator or not left or not right:
        raise argparse.ArgumentTypeError(f"{value!r} is not of the form LEFT=RIGHT")
    return left, right


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--prometheus",
        default="http://127.0.0.1:19090",
        help="Prometheus base URL, normally an SSH port forward to eddings (default: %(default)s).",
    )
    parser.add_argument(
        "--swapped",
        action="append",
        type=pair,
        metavar="NAME=ADDRESS",
        help="A HomePod that swapped addresses (its tracked name) and its CORRECT (current) address; "
        "give exactly two.",
    )
    parser.add_argument(
        "--airplay-name",
        action="append",
        type=pair,
        default=[],
        metavar="NAME=AIRPLAY_NAME",
        help="A HomePod's AirPlay (Bonjour) name when it differs from its tracked name, as in the vault.",
    )
    parser.add_argument("--yes", action="store_true", help="Delete; without it, only print the plan.")
    args = parser.parse_args(argv)
    if args.swapped is None or len(args.swapped) != 2:
        parser.error("give exactly two --swapped NAME=ADDRESS")
    airplay_names = dict(args.airplay_name)
    homepods = [HomePod(name, airplay_names.get(name, name), address) for name, address in args.swapped]

    prometheus = Prometheus(args.prometheus)
    deletions = plan(prometheus, *homepods)
    for deletion in deletions:
        window = f"{utc(deletion.start)} to {utc(deletion.end)}"
        print(f"- {deletion.why}\n    {' '.join(deletion.matchers)}\n    {window}")
    if not args.yes:
        print("\nDry run; re-run with --yes to delete.")
        return 0

    if not prometheus.admin_api_enabled():
        sys.exit("Prometheus is running without --web.enable-admin-api; start it with the override first.")
    for deletion in deletions:
        prometheus.delete_series(deletion.matchers, deletion.start, deletion.end)
        print(f"deleted: {deletion.why}")
    prometheus.clean_tombstones()
    print("tombstones cleaned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
