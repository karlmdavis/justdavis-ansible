"""Tests for 32-bit counter unwrapping."""

from justdavis_monitoring_exporters.common.counters import Unwrapper32

WRAP = 2**32


def test_first_observation_starts_the_total_at_the_raw_value() -> None:
    unwrapper = Unwrapper32()
    assert unwrapper.update("a", 1000) == 1000


def test_increase_is_accumulated() -> None:
    unwrapper = Unwrapper32()
    unwrapper.update("a", 1000)
    assert unwrapper.update("a", 1500) == 1500


def test_wrap_is_detected_and_added() -> None:
    unwrapper = Unwrapper32()
    unwrapper.update("a", WRAP - 100)
    assert unwrapper.update("a", 50) == WRAP - 100 + 150


def test_keys_are_independent() -> None:
    unwrapper = Unwrapper32()
    unwrapper.update("a", 10)
    unwrapper.update("b", 20)
    assert unwrapper.update("a", 15) == 15
    assert unwrapper.update("b", 25) == 25


def test_forgotten_key_restarts_from_raw_instead_of_treating_reset_as_wrap() -> None:
    unwrapper = Unwrapper32()
    unwrapper.update("a", 5000)
    unwrapper.forget_missing(present={"b"})
    assert unwrapper.update("a", 100) == 100


def test_forget_missing_keeps_present_keys() -> None:
    unwrapper = Unwrapper32()
    unwrapper.update("a", 5000)
    unwrapper.forget_missing(present={"a"})
    assert unwrapper.update("a", 5001) == 5001
