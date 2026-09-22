"""Tests for the process plumbing: settings loading, shutdown ordering, and dead-thread detection."""

import threading

import pytest

from justdavis_monitoring_exporters.common.server import configure_logging, load_settings, run_until_stopped
from justdavis_monitoring_exporters.common.settings import SettingsError


def test_load_settings_exits_with_status_2_on_configuration_error(capsys: pytest.CaptureFixture[str]) -> None:
    def loader(_env: object) -> None:
        raise SettingsError("MONITORING_X: bad")

    with pytest.raises(SystemExit) as excinfo:
        load_settings(loader, {})
    assert excinfo.value.code == 2
    assert "MONITORING_X: bad" in capsys.readouterr().err


def test_run_until_stopped_joins_then_cleans_up_then_shuts_down_server() -> None:
    stop = threading.Event()
    order: list[str] = []

    def work() -> None:
        stop.wait()
        order.append("thread-done")

    thread = threading.Thread(target=work)
    threading.Timer(0.05, stop.set).start()
    run_until_stopped(
        scrape_thread=thread,
        stop=stop,
        shutdown_server=lambda: order.append("server"),
        cleanup=lambda: order.append("cleanup"),
        install_signals=False,
        poll_seconds=0.01,
    )
    assert order == ["thread-done", "cleanup", "server"]


def test_run_until_stopped_raises_when_the_scrape_thread_dies_unexpectedly() -> None:
    stop = threading.Event()
    thread = threading.Thread(target=lambda: None)
    with pytest.raises(RuntimeError):
        run_until_stopped(
            scrape_thread=thread,
            stop=stop,
            shutdown_server=lambda: None,
            install_signals=False,
            poll_seconds=0.01,
        )


def test_unknown_log_level_exits_with_status_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        configure_logging({"MONITORING_LOG_LEVEL": "LOUD"})
    assert excinfo.value.code == 2
    assert "MONITORING_LOG_LEVEL" in capsys.readouterr().err
