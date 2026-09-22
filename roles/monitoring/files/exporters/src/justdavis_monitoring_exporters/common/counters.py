"""Unwrapping of 32-bit device counters into monotonic totals.

The AmpliFi router reports per-client byte counters that wrap at 2^32 (observed: a value of exactly
4294967295). Prometheus `rate()` treats a wrap as a counter reset and would undercount one interval, so
the exporter accumulates the deltas itself.

Known limits, by design:

* A genuine reset that leaves the raw value below the previous one is indistinguishable from a wrap and
  overcounts by up to 4 GiB once. The router's counters belong to an association (client on a given
  access point), so callers key on the association and call `forget_missing()` with the keys present in
  each poll: a client that roams, or disappears and comes back, starts a fresh baseline instead.
* More than one wrap between two observations (over ~1.1 Gbit/s sustained at a 30 s poll) is
  undetectable.
* State lives in memory only; an exporter restart restarts every total at the raw value, which
  Prometheus handles as an ordinary counter reset.
"""

from collections.abc import Hashable, Iterable
from dataclasses import dataclass

_WRAP = 2**32


@dataclass(slots=True)
class _State:
    last_raw: int
    total: int


class Unwrapper32[K: Hashable]:
    def __init__(self) -> None:
        self._state: dict[K, _State] = {}

    def update(self, key: K, raw: int) -> int:
        """Record a raw observation for `key` and return its unwrapped cumulative total."""
        if not 0 <= raw < _WRAP:
            raise ValueError(f"counter value {raw} is outside the 32-bit range")
        state = self._state.get(key)
        if state is None:
            self._state[key] = _State(last_raw=raw, total=raw)
            return raw
        delta = raw - state.last_raw
        if delta < 0:
            delta += _WRAP
        state.last_raw = raw
        state.total += delta
        return state.total

    def forget_missing(self, present: Iterable[K]) -> None:
        """Drop state for every key not in `present`, so it restarts from its raw value next time."""
        keep = set(present)
        for key in list(self._state):
            if key not in keep:
                del self._state[key]
