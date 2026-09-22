"""Tests for label sanitising."""

from justdavis_monitoring_exporters.common.labels import sanitise_label


def test_curly_quotes_become_straight_and_control_characters_are_dropped() -> None:
    assert sanitise_label("Guest\u2019s iPad\n") == "Guest's iPad"


def test_long_values_are_capped_and_empty_stays_empty() -> None:
    assert sanitise_label("x" * 100) == "x" * 64
    assert sanitise_label("") == ""
