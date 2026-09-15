"""Tests for the scrape loop: pacing, backoff with jitter, and never dying on errors."""

import threading

from justdavis_monitoring_exporters.common.loop import run_scrape_loop
from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder


class Waiter:
    """Stands in for `stop.wait()`: records requested delays and stops after N waits."""

    def __init__(self, stop: threading.Event, stop_after: int) -> None:
        self.stop = stop
        self.stop_after = stop_after
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> bool:
        self.delays.append(seconds)
        if len(self.delays) >= self.stop_after:
            self.stop.set()
        return self.stop.is_set()


def run(scrape: object, *, interval: float, cap: float, waits: int) -> tuple[list[float], ScrapeStatus]:
    stop = threading.Event()
    waiter = Waiter(stop, waits)
    status: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    status.set(ScrapeStatus.initial())
    run_scrape_loop(
        scrape,  # type: ignore[arg-type]
        interval_seconds=interval,
        backoff_cap_seconds=cap,
        stop=stop,
        status=status,
        wait=waiter,
        jitter=lambda: 1.0,
        clock=lambda: 42.0,
    )
    final = status.get()
    assert final is not None
    return waiter.delays, final


def test_successful_scrapes_wait_the_plain_interval() -> None:
    delays, status = run(lambda: None, interval=30, cap=600, waits=3)
    assert delays == [30, 30, 30]
    assert status.up is True
    assert status.last_success_timestamp == 42.0
    assert status.consecutive_failures == 0


def test_failures_back_off_exponentially_up_to_the_cap() -> None:
    def boom() -> None:
        raise RuntimeError("device unreachable")

    delays, status = run(boom, interval=30, cap=200, waits=5)
    assert delays == [60, 120, 200, 200, 200]
    assert status.up is False
    assert status.consecutive_failures == 5


def test_a_success_after_failures_resets_the_backoff() -> None:
    calls = {"n": 0}

    def flaky() -> None:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("nope")

    delays, status = run(flaky, interval=10, cap=600, waits=4)
    assert delays == [20, 40, 10, 10]
    assert status.up is True
    assert status.consecutive_failures == 0


def test_jitter_scales_the_delay() -> None:
    stop = threading.Event()
    waiter = Waiter(stop, 1)
    status: SnapshotHolder[ScrapeStatus] = SnapshotHolder()
    status.set(ScrapeStatus.initial())
    run_scrape_loop(
        lambda: None,
        interval_seconds=100,
        backoff_cap_seconds=600,
        stop=stop,
        status=status,
        wait=waiter,
        jitter=lambda: 0.9,
        clock=lambda: 0.0,
    )
    assert waiter.delays == [90.0]
