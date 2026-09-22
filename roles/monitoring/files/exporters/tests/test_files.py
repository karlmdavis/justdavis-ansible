"""Tests for atomic, change-only file writes."""

import stat
from pathlib import Path

import pytest

from justdavis_monitoring_exporters.common.files import write_if_changed


def test_creates_missing_file_and_reports_written(tmp_path: Path) -> None:
    target = tmp_path / "targets.yml"
    assert write_if_changed(target, b"a: 1\n") is True
    assert target.read_bytes() == b"a: 1\n"


def test_unchanged_content_is_not_rewritten(tmp_path: Path) -> None:
    target = tmp_path / "targets.yml"
    write_if_changed(target, b"a: 1\n")
    before = target.stat().st_mtime_ns
    assert write_if_changed(target, b"a: 1\n") is False
    assert target.stat().st_mtime_ns == before


def test_changed_content_is_rewritten(tmp_path: Path) -> None:
    target = tmp_path / "targets.yml"
    write_if_changed(target, b"a: 1\n")
    assert write_if_changed(target, b"a: 2\n") is True
    assert target.read_bytes() == b"a: 2\n"


def test_written_file_is_group_readable_but_not_world_readable(tmp_path: Path) -> None:
    target = tmp_path / "targets.yml"
    write_if_changed(target, b"x")
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o640


def test_failed_replace_leaves_no_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(src: str, dst: str) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr("justdavis_monitoring_exporters.common.files.os.replace", refuse)
    with pytest.raises(OSError):
        write_if_changed(tmp_path / "targets.yml", b"x")
    assert list(tmp_path.iterdir()) == []


def test_no_temporary_files_are_left_behind(tmp_path: Path) -> None:
    target = tmp_path / "targets.yml"
    write_if_changed(target, b"x")
    write_if_changed(target, b"y")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["targets.yml"]
