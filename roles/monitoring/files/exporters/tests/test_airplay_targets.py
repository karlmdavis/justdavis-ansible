"""Tests for reading the HomePod addresses back from the AmpliFi exporter's AirPlay target file."""

import logging
from pathlib import Path

import pytest

from justdavis_monitoring_exporters.airplay.targets import AddressBook, parse_airplay_targets
from justdavis_monitoring_exporters.amplifi.parser import parse_info_async
from justdavis_monitoring_exporters.amplifi.targets import render_airplay_targets
from justdavis_monitoring_exporters.common.errors import ParseError
from tests.helpers import FIXTURES, tracked

TRACKED = (
    tracked("02:00:00:00:00:10", "Speaker A", "homepod", "Speaker-A"),
    tracked("02:00:00:00:00:11", "Speaker B", "homepod"),
    tracked("02:00:00:00:00:20", "Tablet", "ipad"),
)


def test_addresses_round_trip_from_the_amplifi_exporter_keyed_by_tracked_name() -> None:
    snapshot = parse_info_async((FIXTURES / "amplifi_info_async.json").read_bytes())
    addresses = parse_airplay_targets(render_airplay_targets(snapshot, TRACKED))
    # The tracked name, not the AirPlay name ("Speaker-A"), and no entry for the non-HomePod.
    assert addresses == {"Speaker A": "192.0.2.110", "Speaker B": "192.0.2.212"}


def test_empty_target_list_means_no_addresses() -> None:
    assert parse_airplay_targets(b"[]\n") == {}


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b'{"targets": []}',
        b'[{"labels": {"name": "Speaker A"}}]',
        b'[{"targets": ["192.0.2.110:7000", "192.0.2.111:7000"], "labels": {"name": "Speaker A"}}]',
        b'[{"targets": ["192.0.2.110"], "labels": {"name": "Speaker A"}}]',
        b'[{"targets": ["192.0.2.110:7000"], "labels": {"kind": "homepod"}}]',
    ],
)
def test_malformed_target_files_raise_parse_error(content: bytes) -> None:
    with pytest.raises(ParseError):
        parse_airplay_targets(content)


def test_address_book_logs_a_missing_file_once_and_recovers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "airplay-targets.json"
    book = AddressBook(path)
    with caplog.at_level(logging.INFO):
        assert book.read() == {}
        assert book.read() == {}
        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert str(path) in warnings[0].getMessage()

        path.write_bytes(
            b'[{"targets": ["192.0.2.110:7000"], "labels": {"name": "Speaker A", "kind": "homepod"}}]'
        )
        assert book.read() == {"Speaker A": "192.0.2.110"}
        assert any(
            record.levelno == logging.INFO and "readable again" in record.getMessage()
            for record in caplog.records
        )

        path.write_bytes(b"{")
        assert book.read() == {}
        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warnings) == 2
