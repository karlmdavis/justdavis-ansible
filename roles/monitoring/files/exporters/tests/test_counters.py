"""Tests for the per-client byte totals built from the router's per-association counters."""

import pytest

from justdavis_monitoring_exporters.common.counters import ClientByteTotals

WRAP = 2**32
RX = ("02:00:00:00:00:10", "rx")
TX = ("02:00:00:00:00:10", "tx")
AP1 = "02:00:00:00:00:01"
AP2 = "02:00:00:00:00:02"


def totals(forget_after_polls: int = 120) -> ClientByteTotals[tuple[str, str], str]:
    return ClientByteTotals(forget_after_polls)


def test_first_reading_starts_the_total_at_the_raw_value() -> None:
    assert totals().observe(RX, AP1, 1000) == 1000


def test_increase_on_the_same_association_is_accumulated() -> None:
    t = totals()
    t.observe(RX, AP1, 1000)
    assert t.observe(RX, AP1, 1500) == 1500


def test_a_roam_adds_the_new_associations_bytes_and_keeps_the_total_climbing() -> None:
    t = totals()
    t.observe(RX, AP1, 5_000_000)
    # The new association's counter started from zero at the roam: 20 kB moved since.
    assert t.observe(RX, AP2, 20_000) == 5_020_000
    assert t.observe(RX, AP2, 30_000) == 5_030_000


def test_a_drop_on_the_same_association_is_a_restarted_counter_not_a_wrap() -> None:
    # Observed 2026-09-23: a band switch on the same access point restarts the association counter; the
    # first version added 2^32 here.
    t = totals()
    t.observe(RX, AP1, 1_148_440)
    assert t.observe(RX, AP1, 98_266) == 1_148_440 + 98_266


def test_unavailable_reading_is_skipped_and_the_total_holds() -> None:
    t = totals()
    t.observe(RX, AP1, 1000)
    assert t.observe(RX, AP1, WRAP - 1) == 1000
    assert t.observe(RX, AP1, WRAP - 1) == 1000
    # Back to a real (lower) reading: the association restarted meanwhile.
    assert t.observe(RX, AP1, 300) == 1300


def test_unavailable_first_reading_starts_the_total_at_zero() -> None:
    assert totals().observe(RX, AP1, WRAP - 1) == 0


def test_directions_and_clients_are_independent() -> None:
    t = totals()
    t.observe(RX, AP1, 10)
    t.observe(TX, AP1, 20)
    assert t.observe(RX, AP1, 15) == 15
    assert t.observe(TX, AP1, 25) == 25


def test_returning_to_a_remembered_association_continues_from_its_last_reading() -> None:
    t = totals()
    t.observe(RX, AP1, 1000)
    t.observe(RX, AP2, 50)
    # Back on AP1 whose counter kept counting while the client was away: only the difference is new.
    assert t.observe(RX, AP1, 1200) == 1000 + 50 + 200


def test_a_client_absent_for_too_long_is_forgotten_and_restarts_from_raw() -> None:
    t = totals(forget_after_polls=2)
    t.observe(RX, AP1, 5000)
    t.end_poll(present=[])
    assert t.observe(RX, AP1, 5100) == 5100
    t.end_poll(present=[])
    t.end_poll(present=[])
    assert t.observe(RX, AP1, 5200) == 5200


def test_present_clients_are_not_counted_absent() -> None:
    t = totals(forget_after_polls=1)
    t.observe(RX, AP1, 5000)
    t.end_poll(present=[RX])
    assert t.observe(RX, AP1, 5001) == 5001


def test_values_outside_the_32_bit_range_are_rejected() -> None:
    with pytest.raises(ValueError, match="32-bit"):
        totals().observe(RX, AP1, WRAP)
