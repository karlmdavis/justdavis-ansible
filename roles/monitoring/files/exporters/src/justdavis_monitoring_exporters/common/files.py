"""Atomic, change-only file writes for files other processes watch (ping_exporter, Prometheus)."""

import os
import tempfile
from pathlib import Path

# Group readable (the `monitoring` group runs the consumers), not world readable, matching the
# permissions the role gives the shared directory.
_MODE = 0o640


def write_if_changed(path: Path, content: bytes) -> bool:
    """Replace `path` with `content` atomically, only if it differs. Returns True when written.

    Comparing against the on-disk content (not an in-memory copy) keeps exporter restarts from
    rewriting unchanged files and needlessly reloading their consumers. The temporary file is created
    in the same directory so `os.replace` is atomic, and its mode is set explicitly because
    `mkstemp` creates it 0600.
    """
    try:
        if path.read_bytes() == content:
            return False
    except FileNotFoundError:
        pass
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
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
