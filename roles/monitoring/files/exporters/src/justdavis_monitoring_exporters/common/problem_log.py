"""Logging of a recurring problem once: when it appears, when its message changes, and when it clears.

A poll loop that meets the same fault every 30 seconds must not write the same warning every 30
seconds; it also must not go quiet when the fault changes shape. Each problem is keyed by the caller
(a file path, a device name) and the log carries the last message recorded for that key.
"""

import logging


class ProblemLog:
    def __init__(self, log: logging.Logger) -> None:
        self._log = log
        self._problems: dict[str, str] = {}

    def problem(self, key: str, message: str) -> None:
        """Warn when `message` is new for `key` (first occurrence or a changed message)."""
        if self._problems.get(key) != message:
            self._log.warning("%s", message)
            self._problems[key] = message

    def clear(self, key: str, message: str) -> None:
        """Log `message` at INFO when `key` had a recorded problem, and forget it."""
        if self._problems.pop(key, None) is not None:
            self._log.info("%s", message)

    def active(self, key: str) -> bool:
        return key in self._problems
