"""Tests for the Comcast gateway (Technicolor CGA4332COM) page parser."""

from pathlib import Path

import pytest

from justdavis_monitoring_exporters.common.errors import ParseError
from justdavis_monitoring_exporters.gateway.parser import is_login_page, parse_comcast_network

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def comcast_network_html() -> str:
    return (FIXTURES / "gateway_comcast_network.html").read_text()


@pytest.fixture(scope="module")
def login_html() -> str:
    return (FIXTURES / "gateway_login.html").read_text()


def test_login_page_is_detected(login_html: str) -> None:
    assert is_login_page(login_html) is True


def test_status_page_is_not_a_login_page(comcast_network_html: str) -> None:
    assert is_login_page(comcast_network_html) is False


def test_parses_system_uptime_into_seconds(comcast_network_html: str) -> None:
    status = parse_comcast_network(comcast_network_html)
    assert status.uptime_seconds == 1 * 3600 + 42 * 60 + 29


def test_parses_internet_and_wan_fields(comcast_network_html: str) -> None:
    status = parse_comcast_network(comcast_network_html)
    assert status.internet_active is True
    assert status.wan_ip == "198.51.100.10"
    assert status.wan_static_ip == "203.0.113.2"
    assert status.isp_gateway == "198.51.100.1"


def test_parses_all_downstream_channels(comcast_network_html: str) -> None:
    status = parse_comcast_network(comcast_network_html)
    assert len(status.downstream) == 34
    assert [c.index for c in status.downstream] == list(range(1, 35))


def test_parses_a_qam_downstream_channel(comcast_network_html: str) -> None:
    status = parse_comcast_network(comcast_network_html)
    first = status.downstream[0]
    assert first.locked is True
    assert first.frequency_hz == 495_000_000
    assert first.snr_db == 44.0
    assert first.power_dbmv == 10.6
    assert first.modulation == "256 QAM"
    assert first.unerrored == 209921024
    assert first.correctable == 182816061
    assert first.uncorrectable == 0


def test_parses_an_ofdm_downstream_channel(comcast_network_html: str) -> None:
    status = parse_comcast_network(comcast_network_html)
    last = status.downstream[-1]
    assert last.index == 34
    assert last.modulation == "OFDM"
    assert last.frequency_hz == 957_000_000
    assert last.snr_db == 39.6
    assert last.power_dbmv == 5.2
    assert last.correctable == 182816061


def test_accepts_bytes_input(comcast_network_html: str) -> None:
    status = parse_comcast_network(comcast_network_html.encode())
    assert status.uptime_seconds == 6149


def test_truncated_page_raises_parse_error(comcast_network_html: str) -> None:
    with pytest.raises(ParseError):
        parse_comcast_network(comcast_network_html[: len(comcast_network_html) // 2])


def test_login_page_raises_parse_error(login_html: str) -> None:
    with pytest.raises(ParseError):
        parse_comcast_network(login_html)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0 days 1h: 42m: 29s", 6149),
        ("3 days 0h: 0m: 5s", 3 * 86400 + 5),
        ("12 days 23h: 59m: 59s", 12 * 86400 + 23 * 3600 + 59 * 60 + 59),
    ],
)
def test_uptime_text_parsing(text: str, expected: int) -> None:
    from justdavis_monitoring_exporters.gateway.parser import parse_uptime

    assert parse_uptime(text) == expected


def test_uptime_text_garbage_raises_parse_error() -> None:
    from justdavis_monitoring_exporters.gateway.parser import parse_uptime

    with pytest.raises(ParseError):
        parse_uptime("forever")


def _with_cells(html: str, row_label: str, first_cell: str) -> str:
    """Replace the first data cell of the given downstream-table row."""
    start = html.index(f'<th class="row-label ">{row_label}</td>')
    cell_start = html.index('<div class="netWidth">', start) + len('<div class="netWidth">')
    cell_end = html.index("</div>", cell_start)
    return html[:cell_start] + first_cell + html[cell_end:]


def test_unlocked_channel_with_placeholder_values_is_kept_with_missing_numbers(
    comcast_network_html: str,
) -> None:
    html = _with_cells(comcast_network_html, "Lock Status", "Not Locked")
    html = _with_cells(html, "SNR", "----")
    html = _with_cells(html, "Power Level", "N/A")
    status = parse_comcast_network(html)
    first = status.downstream[0]
    assert first.locked is False
    assert first.snr_db is None
    assert first.power_dbmv is None
    assert status.downstream[1].snr_db == 43.5


def test_comma_grouped_codeword_counts_are_parsed(comcast_network_html: str) -> None:
    html = _with_cells(comcast_network_html, "Unerrored Codewords", "209,921,024")
    assert parse_comcast_network(html).downstream[0].unerrored == 209921024


def test_unrecognised_lock_status_wording_raises_parse_error(comcast_network_html: str) -> None:
    with pytest.raises(ParseError) as excinfo:
        parse_comcast_network(_with_cells(comcast_network_html, "Lock Status", "Locked?"))
    assert "Lock Status" in str(excinfo.value)


def test_internet_inactive_is_parsed(comcast_network_html: str) -> None:
    html = comcast_network_html.replace(
        '<span class="readonlyLabel">Internet:</span>\n\t\t<span class="value">Active</span>',
        '<span class="readonlyLabel">Internet:</span>\n\t\t<span class="value">Inactive</span>',
    )
    assert parse_comcast_network(html).internet_active is False


def test_unrecognised_internet_wording_raises_parse_error(comcast_network_html: str) -> None:
    html = comcast_network_html.replace(
        '<span class="readonlyLabel">Internet:</span>\n\t\t<span class="value">Active</span>',
        '<span class="readonlyLabel">Internet:</span>\n\t\t<span class="value">Maybe</span>',
    )
    with pytest.raises(ParseError):
        parse_comcast_network(html)
