"""Process plumbing shared by the entry points: registry, HTTP server, signals, and idle handling."""

import logging
import signal
import sys
import threading
from collections.abc import Callable, Mapping

from prometheus_client import CollectorRegistry, start_http_server

from justdavis_monitoring_exporters.common.settings import SettingsError

log = logging.getLogger(__name__)


def configure_logging(env: Mapping[str, str]) -> None:
    level_name = env.get("MONITORING_LOG_LEVEL", "INFO").upper()
    level = logging.getLevelNamesMapping().get(level_name)
    if level is None:
        print(f"configuration error: MONITORING_LOG_LEVEL: unknown level {level_name!r}", file=sys.stderr)
        sys.exit(2)
    logging.basicConfig(
        stream=sys.stderr,
        level=level,
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


def serve(registry: CollectorRegistry, *, addr: str, port: int) -> Callable[[], None]:
    """Start the metrics HTTP server for `registry` on a daemon thread; returns a shutdown callable."""
    server, _thread = start_http_server(port, addr=addr, registry=registry)
    log.info("serving metrics on http://%s:%d/metrics", addr, port)
    return server.shutdown


def run_until_stopped(
    *,
    scrape_thread: threading.Thread | None,
    stop: threading.Event,
    shutdown_server: Callable[[], None],
    cleanup: Callable[[], None] | None = None,
    install_signals: bool = True,
    poll_seconds: float = 1.0,
) -> None:
    """Block the main thread until a signal arrives, then stop everything in order.

    A scrape thread that dies while `stop` is unset is a bug (the loop is designed never to exit), so
    it raises `RuntimeError` and the process exits non-zero for the supervisor to restart, rather than
    serving stale metrics with `<name>_up 1` forever.
    """
    if install_signals:
        install_signal_handlers(stop)
    if scrape_thread is not None:
        scrape_thread.start()
    try:
        while not stop.wait(poll_seconds):
            if scrape_thread is not None and not scrape_thread.is_alive():
                raise RuntimeError("scrape thread exited unexpectedly")
        if scrape_thread is not None:
            scrape_thread.join(timeout=30)
            if scrape_thread.is_alive():
                log.warning("scrape thread did not finish within 30s; exiting anyway")
    finally:
        # Both steps run on every exit path, including the dead-thread error above, and neither may
        # replace that error with its own: a raise here is logged and swallowed.
        if cleanup is not None:
            _guarded(cleanup, "cleanup")
        _guarded(shutdown_server, "metrics server shutdown")
    log.info("stopped")


def _guarded(step: Callable[[], None], what: str) -> None:
    try:
        step()
    except Exception:
        log.exception("%s raised while shutting down", what)
