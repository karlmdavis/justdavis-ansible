"""Tests for the typed JSON narrowing helpers."""

import pytest

from justdavis_monitoring_exporters.common.errors import ParseError
from justdavis_monitoring_exporters.common.jsonutil import as_dict, as_int, as_list, as_str


def test_as_dict_returns_mapping() -> None:
    assert as_dict({"a": 1}, "ctx") == {"a": 1}


def test_as_dict_rejects_non_mapping() -> None:
    with pytest.raises(ParseError) as excinfo:
        as_dict([1], "top")
    assert "top" in str(excinfo.value)


def test_as_list_returns_sequence() -> None:
    assert as_list([1, 2], "ctx") == [1, 2]


def test_as_list_rejects_non_sequence() -> None:
    with pytest.raises(ParseError):
        as_list("abc", "ctx")


def test_as_str_returns_string() -> None:
    assert as_str("x", "ctx") == "x"


def test_as_str_rejects_non_string() -> None:
    with pytest.raises(ParseError):
        as_str(1, "ctx")


def test_as_int_returns_int() -> None:
    assert as_int(7, "ctx") == 7


def test_as_int_rejects_bool() -> None:
    with pytest.raises(ParseError):
        as_int(True, "ctx")


def test_as_int_rejects_float() -> None:
    with pytest.raises(ParseError):
        as_int(7.0, "ctx")
