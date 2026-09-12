"""Process plumbing shared by the entry points: registry, HTTP server, signals, and idle handling."""

import logging
import signal
import sys
import threading
from collections.abc import Callable, Mapping

from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.registry import Collector

from justdavis_monitoring_exporters.common.settings import SettingsError

log = logging.getLogger(__name__)


def configure_logging(env: Mapping[str, str]) -> None:
    level_name = env.get("MONITORING_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Never let request lines (which name login endpoints) reach the log at DEBUG.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def load_settings[T](loader: Callable[[Mapping[str, str]], T], env: Mapping[str, str]) -> T:
    """Build settings or exit with status 2 and a one-line explanation."""
    try:
        return loader(env)
    except SettingsError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        sys.exit(2)


def install_signal_handlers(stop: threading.Event) -> None:
    def request_stop(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def serve(collector: Collector, *, addr: str, port: int) -> Callable[[], None]:
    """Start the metrics HTTP server on a daemon thread; returns a shutdown callable."""
    registry = CollectorRegistry()
    registry.register(collector)
    server, _thread = start_http_server(port, addr=addr, registry=registry)
    log.info("serving metrics on http://%s:%d/metrics", addr, port)
    return server.shutdown


def run_until_stopped(
    *,
    scrape_thread: threading.Thread | None,
    stop: threading.Event,
    shutdown_server: Callable[[], None],
    cleanup: Callable[[], None] | None = None,
) -> None:
    """Block the main thread until a signal arrives, then stop everything in order."""
    install_signal_handlers(stop)
    if scrape_thread is not None:
        scrape_thread.start()
    while not stop.wait(1.0):
        pass
    if scrape_thread is not None:
        scrape_thread.join(timeout=30)
    if cleanup is not None:
        cleanup()
    shutdown_server()
    log.info("stopped")
