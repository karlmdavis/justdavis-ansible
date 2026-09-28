# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""When was audio playing to each HomePod, and did one drop out?

WHEN TO RUN IT

* Music stopped or stuttered on a HomePod and you want to know when, and whether the others in the
  group carried on.
* You want to check whether a HomePod really played all night (a bedtime playlist, white noise).
* You are judging what download rate separates "playing" from "idle" for a kind of stream.

WHAT IT DOES

Reads how much each HomePod downloaded, which the router counts per WiFi client and Prometheus keeps.
Audio arriving at a HomePod shows as a steady download; an idle HomePod downloads almost nothing.

HOW TO RUN IT

On the controller, with `uv` and an SSH port forward to Prometheus; nothing is installed on eddings.
All times are UTC.

    ssh -N -L 19090:127.0.0.1:9090 eddings.justdavis.com &
    uv run roles/monitoring/scripts/homepod_traffic.py
    uv run roles/monitoring/scripts/homepod_traffic.py --hours 36 --threshold 100
    uv run roles/monitoring/scripts/homepod_traffic.py --shape Speaker-A "2026-09-24 05:00" "2026-09-24 05:20"

SAMPLE OUTPUT, DEFAULT VIEW (names are examples)

    == download kbit/s per HomePod, 5-minute buckets at or above 40 kbit/s, 2026-09-27 14:01 to ...
       Speaker-A          09-27 14:01 to 09-27 20:01 ~255k
       Speaker-B          09-27 16:36 to 09-27 16:36 ~73k; 09-27 17:16 to 09-27 17:16 ~54k
       Speaker-C          quiet (below 40 kbit/s throughout)

Each entry is a stretch of time the HomePod downloaded at or above the threshold, with its average
rate. Speaker A played for the whole six hours. Speaker B only crossed the line for single 5-minute
buckets, which is background chatter or a stream sitting right at the threshold: lower `--threshold`
to see which. Speaker C was idle.

SAMPLE OUTPUT, `--shape`

    == Speaker-A: download kbit/s, 30-second samples, 2026-09-27 19:40 to 2026-09-27 19:50 UTC
       min 42  mean 65  max 99
       94 97 99 45 49 44 48 55 42 43 48 42 44 53 42 92 91 84 77 84 92

One number per 30 seconds. The name is the one the default view prints. A drop to near zero in the
middle of a stream is a dropout; compare the same window for the other HomePods in the group.

WHAT THE RATES LOOKED LIKE (2026-09-23/24; not yet checked against controlled playback)

* AirPlay from a phone or iPad: 60-450 kbit/s, and every HomePod in the group moves in lockstep. A
  group member whose curve stops tracking the others has left the group.
* Apple Music playing on the HomePods themselves: fetched per track, 25 kbit/s to 1.4 Mbit/s, about 500
  on average; an overnight playlist held a steady 50-90 kbit/s.
* Idle: 0-20 kbit/s.
"""

import argparse
import calendar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass

# `rx` is what the client received: its download. Summed by name so a roam between access points, which
# splits nothing today but did split the raw counters' history, cannot hide part of the stream.
DOWNLOAD_KBIT_PER_SECOND = (
    'sum by (name) (rate(amplifi_client_rx_bytes_total{{kind="homepod"{extra}}}[{window}])) * 8 / 1000'
)
BUCKET_SECONDS = 300
SHAPE_STEP_SECONDS = 30


@dataclass(frozen=True, slots=True)
class Series:
    labels: dict[str, str]
    # (unix time, value) pairs, oldest first.
    values: list[tuple[float, float]]


def query_range(base_url: str, expr: str, start: float, end: float, step: int) -> list[Series]:
    params = urllib.parse.urlencode(
        {"query": expr, "start": f"{start:.0f}", "end": f"{end:.0f}", "step": str(step)}
    )
    try:
        with urllib.request.urlopen(
            f"{base_url.rstrip('/')}/api/v1/query_range?{params}", timeout=60
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        # Prometheus answers a query it will not run with an error status and the reason in the body.
        sys.exit(f"Prometheus rejected the query: HTTP {exc.code}: {exc.read().decode(errors='replace')}")
    except OSError as exc:
        sys.exit(f"cannot reach Prometheus at {base_url} ({exc}); is the SSH port forward running?")
    if payload.get("status") != "success":
        sys.exit(f"Prometheus rejected the query: {payload}")
    return [
        Series(
            {str(key): str(value) for key, value in series["metric"].items()},
            [(float(timestamp), float(value)) for timestamp, value in series["values"]],
        )
        for series in payload["data"]["result"]
    ]


def promql_string(text: str) -> str:
    """`text` as a double-quoted PromQL string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def utc(timestamp: float, fmt: str = "%m-%d %H:%M") -> str:
    return time.strftime(fmt, time.gmtime(timestamp))


def parse_utc(text: str) -> float:
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%d %H:%M"))
    except ValueError:
        sys.exit(f"expected a UTC time like '2026-09-24 05:00', got {text!r}")


def runs_at_or_above(
    values: Sequence[tuple[float, float]], threshold: float
) -> list[tuple[float, float, float]]:
    """(first bucket, last bucket, mean) for each run of consecutive buckets at or above the threshold."""
    runs: list[tuple[float, float, float]] = []
    current: list[tuple[float, float]] = []
    for timestamp, value in values:
        continues = current and timestamp - current[-1][0] <= BUCKET_SECONDS
        if value >= threshold and (continues or not current):
            current.append((timestamp, value))
            continue
        if current:
            runs.append((current[0][0], current[-1][0], sum(v for _, v in current) / len(current)))
        current = [(timestamp, value)] if value >= threshold else []
    if current:
        runs.append((current[0][0], current[-1][0], sum(v for _, v in current) / len(current)))
    return runs


def print_runs(base_url: str, hours: float, threshold: float) -> None:
    end = time.time()
    start = end - hours * 3600
    expr = DOWNLOAD_KBIT_PER_SECOND.format(extra="", window="5m")
    print(
        f"== download kbit/s per HomePod, 5-minute buckets at or above {threshold:.0f} kbit/s, "
        f"{utc(start, '%Y-%m-%d %H:%M')} to {utc(end, '%Y-%m-%d %H:%M')} UTC"
    )
    for series in sorted(
        query_range(base_url, expr, start, end, BUCKET_SECONDS), key=lambda s: s.labels["name"]
    ):
        runs = runs_at_or_above(series.values, threshold)
        described = "; ".join(f"{utc(first)} to {utc(last)} ~{mean:.0f}k" for first, last, mean in runs)
        print(
            f"   {series.labels['name']:18} {described or f'quiet (below {threshold:.0f} kbit/s throughout)'}"
        )


def print_shape(base_url: str, name: str, start: float, end: float) -> None:
    expr = DOWNLOAD_KBIT_PER_SECOND.format(extra=f", name={promql_string(name)}", window="1m")
    result = query_range(base_url, expr, start, end, SHAPE_STEP_SECONDS)
    values = [value for _, value in result[0].values] if result else []
    window = f"{utc(start, '%Y-%m-%d %H:%M')} to {utc(end, '%Y-%m-%d %H:%M')} UTC"
    print(f"== {name}: download kbit/s, 30-second samples, {window}")
    if not values:
        print("   no data (NAME is the tracked-client name the default view prints, not the AirPlay name)")
        return
    print(f"   min {min(values):.0f}  mean {sum(values) / len(values):.0f}  max {max(values):.0f}")
    print("   " + " ".join(f"{value:.0f}" for value in values))


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
        "--hours", type=float, default=14, help="How far back to look (default: %(default)s)."
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=40,
        help="kbit/s a bucket must reach to count (default: %(default)s).",
    )
    parser.add_argument(
        "--shape",
        nargs=3,
        metavar=("NAME", "START", "END"),
        help="Print one HomePod's 30-second samples between two UTC times ('YYYY-MM-DD HH:MM'); NAME is the "
        "tracked-client name the default view prints.",
    )
    args = parser.parse_args(argv)

    if args.shape:
        name, start, end = args.shape
        print_shape(args.prometheus, name, parse_utc(start), parse_utc(end))
    else:
        print_runs(args.prometheus, args.hours, args.threshold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
