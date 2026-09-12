"""The scrape loop shared by all exporters: paced, backing off on failure, and never exiting on error."""

import logging
import random
import threading
import time
from collections.abc import Callable

from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder

log = logging.getLogger(__name__)


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
            delay = min(interval_seconds * 2**current.consecutive_failures, backoff_cap_seconds)
        else:
            duration = time.monotonic() - started
            status.set(current.succeeded(timestamp=clock(), duration_seconds=duration))
            delay = interval_seconds
        if wait_fn(delay * jitter()):
            return
