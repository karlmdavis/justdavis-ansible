"""Monotonic per-client byte totals from the router's per-association counters.

The AmpliFi router reports RxBytes/TxBytes per association (a client on one access point radio), as
32-bit values. Watched over two days in production (2026-09-23/24) they behave like this:

* An association's counter starts near zero when the association starts, and restarts whenever the
  client re-associates: a roam to another access point, a band switch on the same access point, or a
  silent re-association on the same radio (one seen mid-morning with no other change).
* Some associations report exactly 4294967295 (2^32 - 1) for hours on end (three HomePods and both
  backhaul links at once), a saturated or unavailable reading rather than a wrap in progress.
* No genuine wrap has been observed: at the rates these clients move, wrapping a 32-bit counter within
  one association would take days.

The first version of this module keyed state by association and treated any drop as a wrap, so a band
switch on the same access point added 4 GiB to the total; one phone "downloaded 10 GB" in a day that way.
This version keeps one running total per client and direction:

* A raw value at or above the last one for the same association adds the difference.
* A raw value below it, or the first reading of an association, adds the raw value in full: the
  association's counter (re)started from zero, so the raw value is what moved since then. A genuine
  wrap is therefore counted as a restart and loses the bytes between the last reading and the wrap,
  at most one interval's worth.
* 4294967295 is skipped and the total holds.
* The total never goes backwards while the client is remembered. A client absent from every association
  for `forget_after_polls` polls is forgotten (rotating private MAC addresses would otherwise
  accumulate), and starts again from its raw value when it returns, which Prometheus handles as an
  ordinary counter reset. State lives in memory only; an exporter restart does the same.
"""

from collections.abc import Hashable, Iterable

_WRAP = 2**32
# The router holds this exact value for some associations for hours: not a counter reading.
_UNAVAILABLE = _WRAP - 1


class ClientByteTotals[C: Hashable, A: Hashable]:
    """`C` identifies the client and direction (the exported series), `A` the association it is on."""

    def __init__(self, forget_after_polls: int) -> None:
        self._forget_after_polls = forget_after_polls
        self._last_raw: dict[tuple[C, A], int] = {}
        self._total: dict[C, int] = {}
        self._absent_polls: dict[C, int] = {}

    def observe(self, client: C, association: A, raw: int) -> int:
        """Record a raw counter reading for the client on that association; return the client's total."""
        if not 0 <= raw < _WRAP:
            raise ValueError(f"counter value {raw} is outside the 32-bit range")
        self._absent_polls[client] = 0
        total = self._total.get(client, 0)
        if raw == _UNAVAILABLE:
            self._total[client] = total
            return total
        last = self._last_raw.get((client, association))
        delta = raw if last is None or raw < last else raw - last
        self._last_raw[(client, association)] = raw
        total += delta
        self._total[client] = total
        return total

    def end_poll(self, present: Iterable[C]) -> None:
        """Count a poll against every remembered client not in `present`; forget those absent too long."""
        keep = set(present)
        for client in list(self._total):
            if client in keep:
                continue
            self._absent_polls[client] = self._absent_polls.get(client, 0) + 1
            if self._absent_polls[client] >= self._forget_after_polls:
                self.forget(client)

    def forget(self, client: C) -> None:
        self._total.pop(client, None)
        self._absent_polls.pop(client, None)
        for key in [key for key in self._last_raw if key[0] == client]:
            del self._last_raw[key]
