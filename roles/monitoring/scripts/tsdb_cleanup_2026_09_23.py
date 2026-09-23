"""One-off Prometheus cleanup after PR #132 deploys: delete the first day's series that tell a false
story about the network (the PR description has the table). Kept as a worked example for the next
cleanup: the shape is "one row per false story, with a matcher and an end bound read from Prometheus
itself, so nothing after the fix is touched".

Run on eddings, with the admin API on only for the duration (see prometheus-admin-api.yml):

    cd roles/monitoring/scripts
    scp tsdb_cleanup_2026_09_23.py prometheus-admin-api.yml eddings.justdavis.com:/tmp/
    cd /opt/monitoring
    sudo docker compose -f docker-compose.yml -f /tmp/prometheus-admin-api.yml up -d prometheus
    python3 /tmp/tsdb_cleanup_2026_09_23.py          # prints the plan
    python3 /tmp/tsdb_cleanup_2026_09_23.py --yes    # deletes, then cleans tombstones
    sudo docker compose up -d prometheus             # back to the normal command line
"""

import json
import sys
import time
import urllib.parse
import urllib.request

PROM = "http://127.0.0.1:9090"
LOOKBACK = "3d"


def api(
    path: str, params: dict[str, object] | None = None, method: str = "GET"
) -> object:
    url = f"{PROM}{path}"
    data = None
    if params:
        encoded = urllib.parse.urlencode(params, doseq=True)
        if method == "GET":
            url += "?" + encoded
        else:
            data = encoded.encode()
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        return json.loads(body) if body else None


def first_time(expr: str) -> float | None:
    """Timestamp of the earliest sample matching `expr` in the lookback window (None if none)."""
    result = api(
        "/api/v1/query", {"query": f"min_over_time(timestamp({expr})[{LOOKBACK}:15s])"}
    )
    assert isinstance(result, dict)
    values = [float(r["value"][1]) for r in result["data"]["result"]]
    return min(values) if values else None


def hm(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


flags = api("/api/v1/status/flags")
assert isinstance(flags, dict)
if flags["data"].get("web.enable-admin-api") != "true":
    sys.exit(
        "Prometheus is running without --web.enable-admin-api; start it with the override first."
    )

deploy = first_time("amplifi_mesh_point_online")
amplifi_ok = first_time("amplifi_up == 1")
gateway_ok = first_time("gateway_up == 1")
parents_ok = first_time(
    """airplay_service_resolved{name="Parents' Room", service="_airplay._tcp"} == 1"""
)
for label, ts in (
    ("deploy", deploy),
    ("amplifi_ok", amplifi_ok),
    ("gateway_ok", gateway_ok),
    ("parents_ok", parents_ok),
):
    if ts is None:
        sys.exit(f"could not determine {label}; refusing to guess")
assert deploy and amplifi_ok and gateway_ok and parents_ok

# (description, matchers, end bound or None for "everything")
plan: list[tuple[str, list[str], float | None]] = [
    (
        "channel 1 codeword counters were the firmware's copy of channel 34's",
        ['{__name__=~"gateway_docsis_downstream_.*_codewords_total", channel="1"}'],
        deploy,
    ),
    (
        "the ISP first-hop ping target never answered (stale address, drops ICMP)",
        ['{target="69.140.0.1"}', '{ip="69.140.0.1"}'],
        None,
    ),
    (
        "per-channel DOCSIS alerts flapped on scrape gaps and a threshold since revised",
        [
            '{__name__=~"ALERTS|ALERTS_FOR_STATE", alertname=~"DocsisPowerOutOfRange|DocsisSnrLow"}'
        ],
        deploy,
    ),
    (
        "Parents' Room did not resolve only because its Bonjour name had a curly apostrophe",
        [
            """{__name__=~"airplay_.*", name="Parents' Room"}""",
            """{__name__=~"ALERTS|ALERTS_FOR_STATE", name="Parents' Room"}""",
        ],
        parents_ok - 1,
    ),
    (
        "deploy window: the AmpliFi exporter before its first successful poll",
        [
            '{job="amplifi"}',
            '{__name__=~"ALERTS|ALERTS_FOR_STATE", alertname="AmpliFiScrapeFailing"}',
        ],
        amplifi_ok - 1,
    ),
    (
        "deploy window: the gateway exporter before its first successful poll",
        [
            '{job="gateway"}',
            '{__name__=~"ALERTS|ALERTS_FOR_STATE", alertname="GatewayScrapeFailing"}',
        ],
        gateway_ok - 1,
    ),
]

print(
    f"deploy of the fix seen at {hm(deploy)}; amplifi first ok {hm(amplifi_ok)}; "
    f"gateway first ok {hm(gateway_ok)}; Parents' Room first resolved {hm(parents_ok)}\n"
)
for why, matchers, end in plan:
    print(
        f"- {why}\n    {' '.join(matchers)}\n    up to: {'all samples' if end is None else hm(end)}"
    )

if "--yes" not in sys.argv:
    print("\nDry run; re-run with --yes to delete.")
    sys.exit(0)

for why, matchers, end in plan:
    params: dict[str, object] = {"match[]": matchers}
    if end is not None:
        params["end"] = f"{end:.3f}"
    api("/api/v1/admin/tsdb/delete_series", params, method="POST")
    print("deleted:", why)
api("/api/v1/admin/tsdb/clean_tombstones", method="POST")
print("tombstones cleaned")
