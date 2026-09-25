# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""How bad were the DOCSIS uncorrectable codewords, really? Three views over the last `--hours`: the
DocsisUncorrectableCodewords* alert episodes; each channel's uncorrectable total as a share of every
codeword it carried; and the 5-minute buckets that had any, next to what the WAN ping ladder and the
channels' SNR saw at the same moment. A cable line problem shows as loss or RTT spikes in the same
buckets; on 2026-09-24 the worst bucket was 0.02 % of an OFDM channel's codewords with 0 % WAN loss.

Runs on the controller against Prometheus over an SSH port forward:

    ssh -N -L 19090:127.0.0.1:9090 eddings.justdavis.com &
    uv run roles/monitoring/scripts/docsis_uncorrectables.py
    uv run roles/monitoring/scripts/docsis_uncorrectables.py --hours 72 --wan-target 8.8.8.8
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass

BUCKET_SECONDS = 300
# Alert samples further apart than this belong to separate episodes (the rules evaluate every 30 s).
EPISODE_GAP_SECONDS = 90


@dataclass(frozen=True, slots=True)
class Series:
    labels: dict[str, str]
    # (unix time, value) pairs, oldest first; one pair for an instant query.
    values: list[tuple[float, float]]


def _parse(payload: dict[str, object]) -> list[Series]:
    if payload.get("status") != "success":
        sys.exit(f"Prometheus rejected the query: {payload}")
    data = payload["data"]
    assert isinstance(data, dict)
    parsed: list[Series] = []
    for series in data["result"]:
        samples = series["values"] if "values" in series else [series["value"]]
        parsed.append(
            Series(
                {str(key): str(value) for key, value in series["metric"].items()},
                [(float(timestamp), float(value)) for timestamp, value in samples],
            )
        )
    return parsed


def _get(base_url: str, path: str, params: dict[str, str]) -> list[Series]:
    url = f"{base_url.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = json.load(response)
    assert isinstance(payload, dict)
    return _parse(payload)


def query(base_url: str, expr: str) -> list[Series]:
    return _get(base_url, "/api/v1/query", {"query": expr})


def query_range(base_url: str, expr: str, start: float, end: float, step: int) -> list[Series]:
    params = {"query": expr, "start": f"{start:.0f}", "end": f"{end:.0f}", "step": str(step)}
    return _get(base_url, "/api/v1/query_range", params)


def utc(timestamp: float) -> str:
    return time.strftime("%m-%d %H:%M", time.gmtime(timestamp))


def print_episodes(base_url: str, start: float, end: float) -> None:
    print("== alert episodes (firing)")
    expr = 'ALERTS{alertname=~"DocsisUncorrectableCodewords.*", alertstate="firing"}'
    episodes: list[tuple[float, float, str, str]] = []
    for series in query_range(base_url, expr, start, end, 30):
        timestamps = [timestamp for timestamp, _ in series.values]
        first = previous = timestamps[0]
        for timestamp in timestamps[1:]:
            if timestamp - previous > EPISODE_GAP_SECONDS:
                episodes.append(
                    (first, previous, series.labels["alertname"], series.labels.get("channel", "-"))
                )
                first = timestamp
            previous = timestamp
        episodes.append((first, previous, series.labels["alertname"], series.labels.get("channel", "-")))
    for first, last, alertname, channel in sorted(episodes):
        print(f"   {utc(first)} to {utc(last)}  {alertname} channel {channel}")
    if not episodes:
        print("   none")


def print_channel_shares(base_url: str, hours: float) -> None:
    print(f"== uncorrectable codewords per channel over {hours:g} h, as a share of that channel's codewords")
    window = f"{hours * 3600:.0f}s"

    def increase(kind: str) -> dict[str, float]:
        result = query(base_url, f"increase(gateway_docsis_downstream_{kind}_codewords_total[{window}])")
        return {series.labels["channel"]: series.values[0][1] for series in result}

    uncorrectable = increase("uncorrectable")
    correctable = increase("correctable")
    unerrored = increase("unerrored")
    info = {
        series.labels["channel"]: series.labels
        for series in query(base_url, "gateway_docsis_downstream_info")
    }
    shown = False
    for channel, count in sorted(uncorrectable.items(), key=lambda item: int(item[0])):
        if count <= 0:
            continue
        shown = True
        total = count + correctable.get(channel, 0.0) + unerrored.get(channel, 0.0)
        share = count / total * 100 if total else float("nan")
        labels = info.get(channel, {})
        frequency_mhz = float(labels.get("frequency_hz", "0")) / 1e6
        print(
            f"   channel {channel:>3} {labels.get('modulation', '?'):>8} {frequency_mhz:6.0f} MHz  "
            f"{count:10.0f} uncorrectable  ({share:.5f} % of codewords)"
        )
    if not shown:
        print("   none")


def print_buckets(base_url: str, start: float, end: float, wan_target: str) -> None:
    print(
        f"== 5-minute buckets with uncorrectables (all channels summed), with WAN ping to {wan_target}, SNR"
    )
    buckets: dict[float, float] = {}
    expr = f"increase(gateway_docsis_downstream_uncorrectable_codewords_total[{BUCKET_SECONDS}s])"
    for series in query_range(base_url, expr, start, end, BUCKET_SECONDS):
        for timestamp, value in series.values:
            if value > 0.5:
                buckets[timestamp] = buckets.get(timestamp, 0.0) + value
    if not buckets:
        print("   none")
        return

    def by_time(expr: str, scale: float) -> dict[float, float]:
        """Timestamp -> scaled value of a query that aggregates to a single series."""
        result = query_range(base_url, expr, start, end, BUCKET_SECONDS)
        return {timestamp: value * scale for series in result for timestamp, value in series.values}

    target = f'{{target="{wan_target}"}}'
    window = f"[{BUCKET_SECONDS}s]"
    loss = by_time(f"max(max_over_time(ping_loss_ratio{target}{window}))", 100)
    rtt = by_time(f"max(max_over_time(ping_rtt_mean_seconds{target}{window}))", 1000)
    snr = by_time(f"min(min_over_time(gateway_docsis_downstream_snr_db{window}))", 1)
    nan = float("nan")
    for timestamp in sorted(buckets):
        print(
            f"   {utc(timestamp)}  +{buckets[timestamp]:7.0f} uncorrectable   "
            f"WAN max loss {loss.get(timestamp, nan):5.1f} %  max RTT {rtt.get(timestamp, nan):6.1f} ms   "
            f"min SNR {snr.get(timestamp, nan):.1f} dB"
        )


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
        "--hours", type=float, default=24, help="How far back to look (default: %(default)s)."
    )
    parser.add_argument(
        "--wan-target",
        default="1.1.1.1",
        help="Ping target whose loss and RTT to show beside each bucket (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    end = time.time()
    start = end - args.hours * 3600
    print_episodes(args.prometheus, start, end)
    print_channel_shares(args.prometheus, args.hours)
    print_buckets(args.prometheus, start, end, args.wan_target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
