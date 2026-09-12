"""Unwrapping of 32-bit device counters into monotonic totals.

The AmpliFi router reports per-client byte counters that wrap at 2^32 (observed: a value of exactly
4294967295). Prometheus `rate()` treats a wrap as a counter reset and would undercount one interval, so
the exporter accumulates the deltas itself.

Known limits, by design:

* A genuine reset (client re-association) that leaves the raw value below the previous one is
  indistinguishable from a wrap and overcounts by up to 4 GiB once. Callers mitigate this by calling
  `forget_missing()` with the set of keys present in each poll, so a client that disappeared and came
  back starts a fresh baseline instead.
* More than one wrap between two observations (over ~1.1 Gbit/s sustained at a 30 s poll) is
  undetectable.
* State lives in memory only; an exporter restart restarts every total at the raw value, which
  Prometheus handles as an ordinary counter reset.
"""

from collections.abc import Hashable, Iterable

_WRAP = 2**32


class Unwrapper32:
    def __init__(self) -> None:
        self._last_raw: dict[Hashable, int] = {}
        self._total: dict[Hashable, int] = {}

    def update(self, key: Hashable, raw: int) -> int:
        """Record a raw observation for `key` and return its unwrapped cumulative total."""
        last = self._last_raw.get(key)
        if last is None:
            total = raw
        else:
            delta = raw - last
            if delta < 0:
                delta += _WRAP
            total = self._total[key] + delta
        self._last_raw[key] = raw
        self._total[key] = total
        return total

    def forget_missing(self, present: Iterable[Hashable]) -> None:
        """Drop state for every key not in `present`, so it restarts from its raw value next time."""
        keep = set(present)
        for key in list(self._last_raw):
            if key not in keep:
                del self._last_raw[key]
                del self._total[key]
