"""Atomic, change-only file writes for files other processes watch (ping_exporter, Prometheus)."""

import os
import tempfile
from pathlib import Path

_MODE = 0o644


def write_if_changed(path: Path, content: bytes) -> bool:
    """Replace `path` with `content` atomically, only if it differs. Returns True when written.

    Comparing against the on-disk content (not an in-memory copy) keeps exporter restarts from
    rewriting unchanged files and needlessly reloading their consumers. The temporary file is created
    in the same directory so `os.replace` is atomic, and it is made group/world readable before the
    rename because `NamedTemporaryFile` defaults to 0600.
    """
    try:
        if path.read_bytes() == content:
            return False
    except OSError:
        pass
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    try:
        os.chmod(tmp_path, _MODE)
        os.replace(tmp_path, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return True
