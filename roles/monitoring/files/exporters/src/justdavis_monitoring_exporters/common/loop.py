"""The scrape loop shared by all exporters: paced, backing off on failure, and never exiting on error."""

import logging
import random
import threading
import time
from collections.abc import Callable

from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder

log = logging.getLogger(__name__)

# 2**20 times any sane interval is past any sane cap; without a bound the float conversion overflows
# after 1024 consecutive failures (a few days of outage) and kills the loop.
_MAX_BACKOFF_EXPONENT = 20


def default_jitter() -> float:
    return random.uniform(0.9, 1.1)


def run_scrape_loop(
    scrape: Callable[[], None],
    *,
    interval_seconds: float,
    backoff_cap_seconds: float,
    stop: threading.Event,
    status: SnapshotHolder[ScrapeStatus],
    wait: Callable[[float], bool] | None = None,
    jitter: Callable[[], float] = default_jitter,
    clock: Callable[[], float] = time.time,
) -> None:
    """Call `scrape()` until `stop` is set.

    A successful call waits `interval_seconds`; after N consecutive failures the wait grows to
    `interval_seconds * 2**N`, capped at `backoff_cap_seconds`. Every delay is scaled by `jitter()`.
    Exceptions are logged (with traceback) and counted in `status`; the loop itself never raises.
    """
    wait_fn = wait if wait is not None else stop.wait
    while not stop.is_set():
        started = time.monotonic()
        current = status.get() or ScrapeStatus.initial()
        try:
            scrape()
        except Exception:
            duration = time.monotonic() - started
            current = current.failed(duration_seconds=duration)
            status.set(current)
            log.error("scrape failed (%d in a row)", current.consecutive_failures, exc_info=True)
            exponent = min(current.consecutive_failures, _MAX_BACKOFF_EXPONENT)
            delay = min(interval_seconds * 2**exponent, backoff_cap_seconds)
        else:
            duration = time.monotonic() - started
            status.set(current.succeeded(timestamp=clock(), duration_seconds=duration))
            delay = interval_seconds
        if wait_fn(delay * jitter()):
            return


def start_scrape_thread(
    name: str,
    scrape: Callable[[], None],
    *,
    interval_seconds: float,
    backoff_cap_seconds: float,
    stop: threading.Event,
    status: SnapshotHolder[ScrapeStatus],
) -> threading.Thread:
    """A daemon thread (not yet started) that runs `run_scrape_loop` with the production clock."""
    return threading.Thread(
        target=run_scrape_loop,
        kwargs={
            "scrape": scrape,
            "interval_seconds": interval_seconds,
            "backoff_cap_seconds": backoff_cap_seconds,
            "stop": stop,
            "status": status,
            "clock": time.time,
        },
        name=name,
        daemon=True,
    )
