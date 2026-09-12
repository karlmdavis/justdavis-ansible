"""Parser for the Comcast Business gateway's `comcast_network.jst` page.

Verified against a Technicolor CGA4332COM ("CBR2-T") running firmware `CGA4332COM_8.2p11s1_PROD_sey`
in September 2026. The page is server-rendered (no AJAX) and uses two shapes:

* Key/value rows: `<span class="readonlyLabel">System Uptime:</span><span class="value">0 days 1h: 42m:
  29s</span>`.
* Tables with a caption cell (`<td class="row-label acs-th">CM Error Codewords</td>`) and rows whose
  label is a `<th class="row-label">` that the firmware closes with `</td>`, followed by one `<td>` per
  channel. The downstream table's rows are Index, Lock Status, Frequency, SNR, Power Level, Modulation.
  The upstream table has the same row labels but no cells on this firmware, so it is ignored.

Unauthenticated requests get the login page (HTTP 200) instead, recognised by its `id="pageForm"`.
This module is pure: stdlib only, no I/O, and it never returns partial results.
"""

import re
from collections.abc import Sequence
from html.parser import HTMLParser

from justdavis_monitoring_exporters.common.errors import ParseError
from justdavis_monitoring_exporters.gateway.models import DocsisChannel, GatewayStatus

_LOGIN_FORM_MARKER = 'id="pageForm"'
_UPTIME_RE = re.compile(r"(\d+)\s*days?\s+(\d+)h:\s*(\d+)m:\s*(\d+)s")


def is_login_page(html: bytes | str) -> bool:
    """Return True when the gateway answered with its login form instead of the requested page."""
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    return _LOGIN_FORM_MARKER in text


def parse_uptime(text: str) -> int:
    """Turn `"0 days 1h: 42m: 29s"` into seconds."""
    match = _UPTIME_RE.search(text)
    if match is None:
        raise ParseError("System Uptime: unrecognised format")
    days, hours, minutes, seconds = (int(part) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


class _Table:
    def __init__(self) -> None:
        self.caption: str = ""
        self.rows: dict[str, list[str]] = {}


class _PageParser(HTMLParser):
    """Collects key/value rows and tables from the page in one pass."""

    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}
        self.tables: list[_Table] = []
        self._pending_label: str | None = None
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._table: _Table | None = None
        self._row_label: str | None = None
        self._row_cells: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class") or ""
        if tag == "span" and "readonlyLabel" in classes:
            self._start_capture("label")
        elif tag == "span" and "value" in classes:
            self._start_capture("value")
        elif tag == "table":
            self._table = _Table()
            self.tables.append(self._table)
        elif tag == "td" and "acs-th" in classes and self._table is not None:
            self._start_capture("caption")
        elif tag == "th" and self._table is not None:
            self._start_capture("row_label")
        elif tag == "td" and self._table is not None and self._row_label is not None:
            self._start_capture("cell")
        elif tag == "tr" and self._table is not None:
            self._row_label = None
            self._row_cells = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture is None:
            if tag == "tr":
                self._finish_row()
            elif tag == "table":
                self._finish_row()
                self._table = None
            return
        if tag in ("span", "td", "th"):
            self._finish_capture()
            if tag == "tr":
                self._finish_row()

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buffer.append(data)

    def _start_capture(self, kind: str) -> None:
        if self._capture is not None:
            self._finish_capture()
        self._capture = kind
        self._buffer = []

    def _finish_capture(self) -> None:
        text = " ".join("".join(self._buffer).split())
        kind, self._capture = self._capture, None
        if kind == "label":
            self._pending_label = text.rstrip(":")
        elif kind == "value":
            if self._pending_label is not None and self._pending_label not in self.fields:
                self.fields[self._pending_label] = text
            self._pending_label = None
        elif kind == "caption" and self._table is not None:
            self._table.caption = text
        elif kind == "row_label":
            self._row_label = text
            self._row_cells = []
        elif kind == "cell":
            self._row_cells.append(text)

    def _finish_row(self) -> None:
        if self._table is not None and self._row_label is not None:
            self._table.rows[self._row_label] = self._row_cells
        self._row_label = None
        self._row_cells = []


def _field(fields: dict[str, str], name: str) -> str:
    if name not in fields:
        raise ParseError(f"{name}: field not found on page")
    return fields[name]


def _table_with_caption(tables: Sequence[_Table], caption: str) -> _Table:
    for table in tables:
        if table.caption == caption:
            return table
    raise ParseError(f"{caption}: table not found on page")


def _downstream_table(tables: Sequence[_Table]) -> _Table:
    """The downstream channel table is the first one whose Index row is populated."""
    for table in tables:
        if table.rows.get("Index") and table.rows.get("Lock Status"):
            return table
    raise ParseError("Downstream: channel table not found on page")


def _row(table: _Table, label: str, expected_len: int) -> list[str]:
    cells = table.rows.get(label)
    if cells is None:
        raise ParseError(f"{table.caption or 'Downstream'}.{label}: row not found")
    if len(cells) != expected_len:
        raise ParseError(
            f"{table.caption or 'Downstream'}.{label}: expected {expected_len} cells, got {len(cells)}"
        )
    return cells


def _number(text: str, unit: str, ctx: str) -> float:
    value = text.removesuffix(unit).strip()
    try:
        return float(value)
    except ValueError as exc:
        raise ParseError(f"{ctx}: unrecognised number") from exc


def _count(text: str, ctx: str) -> int:
    try:
        return int(text)
    except ValueError as exc:
        raise ParseError(f"{ctx}: unrecognised count") from exc


def _channels(downstream: _Table, codewords: _Table) -> tuple[DocsisChannel, ...]:
    indexes = downstream.rows["Index"]
    count = len(indexes)
    locked = _row(downstream, "Lock Status", count)
    frequency = _row(downstream, "Frequency", count)
    snr = _row(downstream, "SNR", count)
    power = _row(downstream, "Power Level", count)
    modulation = _row(downstream, "Modulation", count)
    unerrored = _row(codewords, "Unerrored Codewords", count)
    correctable = _row(codewords, "Correctable Codewords", count)
    uncorrectable = _row(codewords, "Uncorrectable Codewords", count)
    channels: list[DocsisChannel] = []
    for i in range(count):
        ctx = f"Downstream[{indexes[i]}]"
        channels.append(
            DocsisChannel(
                index=_count(indexes[i], f"{ctx}.Index"),
                locked=locked[i] == "Locked",
                frequency_hz=int(_number(frequency[i], "MHz", f"{ctx}.Frequency") * 1_000_000),
                snr_db=_number(snr[i], "dB", f"{ctx}.SNR"),
                power_dbmv=_number(power[i], "dBmV", f"{ctx}.Power Level"),
                modulation=modulation[i],
                unerrored=_count(unerrored[i], f"{ctx}.Unerrored"),
                correctable=_count(correctable[i], f"{ctx}.Correctable"),
                uncorrectable=_count(uncorrectable[i], f"{ctx}.Uncorrectable"),
            )
        )
    return tuple(channels)


def parse_comcast_network(html: bytes | str) -> GatewayStatus:
    """Parse the "Comcast Network" status page into a `GatewayStatus`."""
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    if is_login_page(text):
        raise ParseError("page: login form returned instead of status page")
    parser = _PageParser()
    parser.feed(text)
    parser.close()
    fields = parser.fields
    downstream = _downstream_table(parser.tables)
    codewords = _table_with_caption(parser.tables, "CM Error Codewords")
    return GatewayStatus(
        uptime_seconds=parse_uptime(_field(fields, "System Uptime")),
        internet_active=_field(fields, "Internet") == "Active",
        wan_ip=_field(fields, "WAN IP Address (IPv4)"),
        wan_static_ip=_field(fields, "WAN Static IP Address (IPv4)"),
        isp_gateway=_field(fields, "WAN Default Gateway Address (IPv4)"),
        downstream=_channels(downstream, codewords),
    )
