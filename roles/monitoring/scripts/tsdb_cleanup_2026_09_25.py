# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""One-off Prometheus cleanup after the AmpliFi client byte counters were made monotonic: delete every
sample of `amplifi_client_rx_bytes_total` and `amplifi_client_tx_bytes_total` written by the earlier
exporter. Those samples jumped by 2^32 whenever an association's counter restarted on the same access
point (read as a 32-bit wrap) and dropped to a small value at every roam, so any rate or increase over
them tells a false story (one phone "downloaded 10 GB" in a day). The end bound is the first sample of
the renamed link-rate series, which only the fixed exporter emits, so nothing after the fix is touched.

Same procedure as tsdb_cleanup_2026_09_23.py: admin API on for the duration, SSH port forward, dry run
by default.

    scp roles/monitoring/scripts/prometheus-admin-api.yml eddings.justdavis.com:/tmp/
    ssh eddings.justdavis.com 'cd /opt/monitoring &&
        sudo docker compose -f docker-compose.yml -f /tmp/prometheus-admin-api.yml up -d prometheus'
    ssh -N -L 19090:127.0.0.1:9090 eddings.justdavis.com &
    uv run roles/monitoring/scripts/tsdb_cleanup_2026_09_25.py            # prints the plan
    uv run roles/monitoring/scripts/tsdb_cleanup_2026_09_25.py --yes      # deletes, cleans tombstones
    ssh eddings.justdavis.com 'cd /opt/monitoring && sudo docker compose up -d prometheus'
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Sequence

LOOKBACK = "3d"
DEPLOY_MARKER = "amplifi_client_rx_link_bits_per_second"
MATCHERS = ('{__name__=~"amplifi_client_(rx|tx)_bytes_total"}',)

type Params = dict[str, str | list[str]]


class Prometheus:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def _call(self, path: str, params: Params | None = None, *, method: str = "GET") -> dict[str, object]:
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

    def delete_series(self, matchers: Sequence[str], end: float) -> None:
        params: Params = {"match[]": list(matchers), "end": f"{end:.3f}"}
        self._call("/api/v1/admin/tsdb/delete_series", params, method="POST")

    def clean_tombstones(self) -> None:
        self._call("/api/v1/admin/tsdb/clean_tombstones", method="POST")


def utc(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(timestamp))


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
    deploy = prometheus.first_sample_time(DEPLOY_MARKER)
    if deploy is None:
        sys.exit(f"no {DEPLOY_MARKER} samples in the last {LOOKBACK}: the fixed exporter is not deployed yet")
    end = deploy - 1
    print(f"- client byte counters written before the monotonic fix\n    {' '.join(MATCHERS)}")
    print(f"    up to {utc(end)}")
    if not args.yes:
        print("\nDry run; re-run with --yes to delete.")
        return 0
    prometheus.delete_series(MATCHERS, end)
    print("deleted")
    prometheus.clean_tombstones()
    print("tombstones cleaned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
