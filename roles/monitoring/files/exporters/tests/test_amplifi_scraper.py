"""Tests for the AmpliFi scraper glue: snapshot publication, error attribution, and target files."""

from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry

from justdavis_monitoring_exporters.amplifi.collector import AmplifiCollector
from justdavis_monitoring_exporters.amplifi.main import (
    AmplifiDevice,
    AmplifiScraper,
    AmplifiSettings,
    build_registry,
)
from justdavis_monitoring_exporters.amplifi.targets import PingConfig
from justdavis_monitoring_exporters.common.errors import LoginError, ParseError
from tests.fakes import FailingHttpClient, FakeHttpClient, response
from tests.helpers import tracked

FIXTURES = Path(__file__).parent / "fixtures"
LOGIN_HTML = (FIXTURES / "amplifi_login.html").read_text()
INFO_HTML = (FIXTURES / "amplifi_info.html").read_text()
INFO_JSON = (FIXTURES / "amplifi_info_async.json").read_bytes()
TRACKED = (tracked("02:00:00:00:00:10", "Speaker A", "homepod"),)


def settings(tmp_path: Path) -> AmplifiSettings:
    return AmplifiSettings(
        device=AmplifiDevice(host="192.0.2.1", password="pw"),
        port=9801,
        listen_addr="127.0.0.1",
        interval_seconds=30,
        tracked_clients=TRACKED,
        static_ping_targets=("1.1.1.1",),
        shared_dir=tmp_path,
        ping=PingConfig(interval_seconds=1, timeout_seconds=2, history_size=60),
    )


def http_with(info_async: bytes) -> FakeHttpClient:
    return FakeHttpClient(
        {
            ("GET", "/login.php"): [response(200, LOGIN_HTML)],
            ("POST", "/login.php"): [response(302, "", location="/index.php")],
            ("GET", "/info.php"): [response(200, INFO_HTML)],
            ("POST", "/info-async.php"): [response(200, info_async)],
        }
    )


def make(
    tmp_path: Path, info_async: bytes = INFO_JSON, http: FakeHttpClient | None = None
) -> tuple[AmplifiScraper, AmplifiCollector, CollectorRegistry]:
    from justdavis_monitoring_exporters.amplifi.client import AmplifiClient

    registry, collector, errors = build_registry(TRACKED)
    client = AmplifiClient(http if http is not None else http_with(info_async), password="pw")
    return AmplifiScraper(settings(tmp_path), client, collector, errors), collector, registry


def test_successful_poll_publishes_snapshot_and_writes_target_files(tmp_path: Path) -> None:
    scraper, _collector, registry = make(tmp_path)
    scraper()
    assert registry.get_sample_value("amplifi_router_uptime_seconds") == 33652.0
    assert (
        registry.get_sample_value(
            "monitoring_target_info", {"ip": "192.0.2.110", "name": "Speaker A", "kind": "homepod"}
        )
        == 1.0
    )
    assert (tmp_path / "ping-targets.yml").read_text().endswith("  - 192.0.2.110\n")
    assert '"192.0.2.110:7000"' in (tmp_path / "airplay-targets.json").read_text()
    assert registry.get_sample_value("amplifi_target_files_ok") == 1.0


def test_unchanged_poll_does_not_rewrite_target_files(tmp_path: Path) -> None:
    scraper, _collector, _registry = make(tmp_path)
    scraper()
    # write_if_changed replaces the file, so an unchanged inode means no rewrite happened.
    before = (tmp_path / "ping-targets.yml").stat().st_ino
    scraper()
    assert (tmp_path / "ping-targets.yml").stat().st_ino == before


def test_login_failure_clears_snapshot_and_counts_login_stage(tmp_path: Path) -> None:
    http = http_with(INFO_JSON)
    scraper, _collector, registry = make(tmp_path, http=http)
    scraper()
    http.script[("GET", "/info.php")] = [response(302, "", location="/login.php")]
    http.script[("POST", "/login.php")] = [response(200, LOGIN_HTML)]
    with pytest.raises(LoginError):
        scraper()
    assert registry.get_sample_value("amplifi_router_uptime_seconds") is None
    assert registry.get_sample_value("amplifi_scrape_errors_total", {"stage": "login"}) == 1.0


def test_parse_failure_clears_snapshot_and_counts_parse_stage(tmp_path: Path) -> None:
    scraper, _collector, registry = make(tmp_path, info_async=b"<html>not json</html>")
    with pytest.raises(ParseError):
        scraper()
    assert registry.get_sample_value("amplifi_router_uptime_seconds") is None
    assert registry.get_sample_value("amplifi_scrape_errors_total", {"stage": "parse"}) == 1.0


def test_target_file_write_failure_is_reported_without_failing_the_poll(tmp_path: Path) -> None:
    scraper, _collector, registry = make(tmp_path / "missing")
    scraper()
    assert registry.get_sample_value("amplifi_router_uptime_seconds") == 33652.0
    assert registry.get_sample_value("amplifi_target_files_ok") == 0.0
    assert registry.get_sample_value("amplifi_scrape_errors_total", {"stage": "write"}) == 1.0


def test_seed_only_creates_missing_target_files(tmp_path: Path) -> None:
    scraper, _collector, _registry = make(tmp_path)
    (tmp_path / "ping-targets.yml").write_text("ping:\n  interval: 1s\ntargets:\n  - 192.0.2.99\n")
    scraper.seed_targets()
    assert "192.0.2.99" in (tmp_path / "ping-targets.yml").read_text()
    assert (tmp_path / "airplay-targets.json").read_text() == "[]\n"


def test_errors_counter_is_served_from_the_same_registry_as_the_collector() -> None:
    registry, _collector, errors = build_registry(TRACKED)
    errors.labels(stage="fetch").inc()
    assert registry.get_sample_value("amplifi_scrape_errors_total", {"stage": "fetch"}) == 1.0


def test_unreachable_router_clears_snapshot_and_counts_fetch_stage(tmp_path: Path) -> None:
    http = FailingHttpClient(http_with(INFO_JSON).script, error=ConnectionError("router unreachable"))
    scraper, _collector, registry = make(tmp_path, http=http)
    scraper()
    http.arm()
    with pytest.raises(ConnectionError):
        scraper()
    assert registry.get_sample_value("amplifi_router_uptime_seconds") is None
    assert registry.get_sample_value("amplifi_scrape_errors_total", {"stage": "fetch"}) == 1.0
    assert registry.get_sample_value("amplifi_scrape_errors_total", {"stage": "login"}) is None


def test_target_files_ok_recovers_once_the_directory_is_writable(tmp_path: Path) -> None:
    scraper, _collector, registry = make(tmp_path / "missing")
    scraper()
    assert registry.get_sample_value("amplifi_target_files_ok") == 0.0
    (tmp_path / "missing").mkdir()
    scraper()
    assert registry.get_sample_value("amplifi_target_files_ok") == 1.0
