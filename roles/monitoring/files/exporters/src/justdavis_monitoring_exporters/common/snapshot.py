"""Immutable snapshots handed from the scrape thread to the Prometheus HTTP thread.

The scrape loop publishes a whole new (frozen) snapshot per successful poll; the collector reads
whichever snapshot is current when Prometheus scrapes. Because a snapshot is never mutated after
publication, a single lock around the reference swap is all the synchronisation needed.
"""

import threading
from dataclasses import dataclass, replace


class SnapshotHolder[T]:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: T | None = None

    def get(self) -> T | None:
        with self._lock:
            return self._value

    def set(self, value: T) -> None:
        with self._lock:
            self._value = value

    def clear(self) -> None:
        with self._lock:
            self._value = None


@dataclass(frozen=True, slots=True)
class ScrapeStatus:
    """Always-present status of a scrape loop, exported even when no snapshot exists."""

    up: bool
    last_success_timestamp: float | None
    last_duration_seconds: float
    consecutive_failures: int

    @classmethod
    def initial(cls) -> "ScrapeStatus":
        return cls(up=False, last_success_timestamp=None, last_duration_seconds=0.0, consecutive_failures=0)

    def succeeded(self, *, timestamp: float, duration_seconds: float) -> "ScrapeStatus":
        return replace(
            self,
            up=True,
            last_success_timestamp=timestamp,
            last_duration_seconds=duration_seconds,
            consecutive_failures=0,
        )

    def failed(self, *, duration_seconds: float) -> "ScrapeStatus":
        return replace(
            self,
            up=False,
            last_duration_seconds=duration_seconds,
            consecutive_failures=self.consecutive_failures + 1,
        )
