# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""One-off Prometheus cleanup after PR #132 deploys: delete the first day's series that tell a false
story about the network (the PR description has the table). Kept as a worked example for the next
cleanup: one `Deletion` per false story, each with its matchers and an end bound read from Prometheus
itself, so nothing after the fix is touched.

Runs on the controller against Prometheus over an SSH port forward, with the admin API on only for
the duration (see prometheus-admin-api.yml):

    scp roles/monitoring/scripts/prometheus-admin-api.yml eddings.justdavis.com:/tmp/
    ssh eddings.justdavis.com 'cd /opt/monitoring &&
        sudo docker compose -f docker-compose.yml -f /tmp/prometheus-admin-api.yml up -d prometheus'
    ssh -N -L 19090:127.0.0.1:9090 eddings.justdavis.com &
    uv run roles/monitoring/scripts/tsdb_cleanup_2026_09_23.py            # prints the plan
    uv run roles/monitoring/scripts/tsdb_cleanup_2026_09_23.py --yes      # deletes, cleans tombstones
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

LOOKBACK = "3d"

type Params = dict[str, str | list[str]]


@dataclass(frozen=True, slots=True)
class Deletion:
    """One false story: the series to delete and how far forward the deletion reaches."""

    why: str
    matchers: tuple[str, ...]
    # Unix time; None deletes every sample of the matched series.
    end: float | None


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
        with urllib.request.urlopen(request, timeout=60) as response:
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

    def first_sample_time(self, expr: str) -> float | None:
        """Unix time of the earliest sample matching `expr` in the lookback window, if any."""
        query = f"min_over_time(timestamp({expr})[{LOOKBACK}:15s])"
        result = self._data("/api/v1/query", {"query": query})["result"]
        if not isinstance(result, list):
            raise TypeError("/api/v1/query: expected a list under 'data.result'")
        times: list[float] = []
        for series in result:
            if not isinstance(series, dict):
                raise TypeError("/api/v1/query: expected a JSON object per series")
            _, value = series["value"]
            times.append(float(value))
        return min(times) if times else None

    def delete_series(self, matchers: Sequence[str], end: float | None) -> None:
        params: Params = {"match[]": list(matchers)}
        if end is not None:
            params["end"] = f"{end:.3f}"
        self._call("/api/v1/admin/tsdb/delete_series", params, method="POST")

    def clean_tombstones(self) -> None:
        self._call("/api/v1/admin/tsdb/clean_tombstones", method="POST")


def utc(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(timestamp))


def bound(prometheus: Prometheus, label: str, expr: str) -> float:
    """The first time `expr` was true, or exit: a missing bound means the fix is not deployed yet
    (or the window has passed), and guessing would delete the wrong samples."""
    found = prometheus.first_sample_time(expr)
    if found is None:
        sys.exit(f"could not determine the {label} bound from {expr!r}; refusing to guess")
    return found


def plan(prometheus: Prometheus) -> list[Deletion]:
    deploy = bound(prometheus, "deploy", "amplifi_mesh_point_online")
    amplifi_ok = bound(prometheus, "amplifi first success", "amplifi_up == 1")
    gateway_ok = bound(prometheus, "gateway first success", "gateway_up == 1")
    parents_ok = bound(
        prometheus,
        "Parents' Room first resolve",
        """airplay_service_resolved{name="Parents' Room", service="_airplay._tcp"} == 1""",
    )
    alerts = '__name__=~"ALERTS|ALERTS_FOR_STATE"'
    return [
        Deletion(
            "channel 1 codeword counters were the firmware's copy of channel 34's",
            ('{__name__=~"gateway_docsis_downstream_.*_codewords_total", channel="1"}',),
            deploy,
        ),
        Deletion(
            "the ISP first-hop ping target never answered (stale address, drops ICMP)",
            ('{target="69.140.0.1"}', '{ip="69.140.0.1"}'),
            None,
        ),
        Deletion(
            "per-channel DOCSIS alerts flapped on scrape gaps and a threshold since revised",
            (f'{{{alerts}, alertname=~"DocsisPowerOutOfRange|DocsisSnrLow"}}',),
            deploy,
        ),
        Deletion(
            "Parents' Room did not resolve only because its Bonjour name had a curly apostrophe",
            ("""{__name__=~"airplay_.*", name="Parents' Room"}""", f"""{{{alerts}, name="Parents' Room"}}"""),
            parents_ok - 1,
        ),
        Deletion(
            "deploy window: the AmpliFi exporter before its first successful poll",
            ('{job="amplifi"}', f'{{{alerts}, alertname="AmpliFiScrapeFailing"}}'),
            amplifi_ok - 1,
        ),
        Deletion(
            "deploy window: the gateway exporter before its first successful poll",
            ('{job="gateway"}', f'{{{alerts}, alertname="GatewayScrapeFailing"}}'),
            gateway_ok - 1,
        ),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--prometheus",
        default="http://127.0.0.1:19090",
        help="Prometheus base URL, normally an SSH port forward to eddings (default: %(default)s).",
    )
    parser.add_argument("--yes", action="store_true", help="Delete; without it, only print the plan.")
    args = parser.parse_args(argv)

    prometheus = Prometheus(args.prometheus)
    if not prometheus.admin_api_enabled():
        sys.exit("Prometheus is running without --web.enable-admin-api; start it with the override first.")

    deletions = plan(prometheus)
    for deletion in deletions:
        reach = "all samples" if deletion.end is None else f"up to {utc(deletion.end)}"
        print(f"- {deletion.why}\n    {' '.join(deletion.matchers)}\n    {reach}")
    if not args.yes:
        print("\nDry run; re-run with --yes to delete.")
        return 0

    for deletion in deletions:
        prometheus.delete_series(deletion.matchers, deletion.end)
        print(f"deleted: {deletion.why}")
    prometheus.clean_tombstones()
    print("tombstones cleaned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
