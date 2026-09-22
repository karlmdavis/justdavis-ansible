"""Metric families shared by every exporter's collector."""

from collections.abc import Iterator
from typing import Literal

from prometheus_client import Counter
from prometheus_client.core import GaugeMetricFamily, Metric

from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus


def status_families(prefix: str, status: ScrapeStatus | None) -> Iterator[Metric]:
    """Always-present status metrics: `<prefix>_up`, last success time, duration, failure streak."""
    current = status or ScrapeStatus.initial()
    yield GaugeMetricFamily(
        f"{prefix}_up", "1 when the last scrape of the device succeeded.", value=float(current.up)
    )
    yield GaugeMetricFamily(
        f"{prefix}_last_success_timestamp_seconds",
        "Unix time of the last successful scrape (0 if none yet).",
        value=current.last_success_timestamp or 0.0,
    )
    yield GaugeMetricFamily(
        f"{prefix}_scrape_duration_seconds",
        "Duration of the most recent scrape attempt.",
        value=current.last_duration_seconds,
    )
    yield GaugeMetricFamily(
        f"{prefix}_consecutive_failures",
        "Number of consecutive failed scrape attempts.",
        value=float(current.consecutive_failures),
    )


# The step of a poll that failed, as the `stage` label of `<prefix>_scrape_errors_total`.
type ErrorStage = Literal["login", "fetch", "tls", "parse", "write", "resolve"]


def count_error(errors: Counter, stage: ErrorStage) -> None:
    errors.labels(stage=stage).inc()
