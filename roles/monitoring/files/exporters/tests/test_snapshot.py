"""Tests for the snapshot holder shared between the scrape thread and the HTTP thread."""

from justdavis_monitoring_exporters.common.snapshot import ScrapeStatus, SnapshotHolder


def test_holder_starts_empty() -> None:
    holder: SnapshotHolder[int] = SnapshotHolder()
    assert holder.get() is None


def test_holder_returns_last_set_value() -> None:
    holder: SnapshotHolder[int] = SnapshotHolder()
    holder.set(1)
    holder.set(2)
    assert holder.get() == 2


def test_holder_can_be_cleared() -> None:
    holder: SnapshotHolder[int] = SnapshotHolder()
    holder.set(1)
    holder.clear()
    assert holder.get() is None


def test_initial_status_is_down_with_no_success() -> None:
    status = ScrapeStatus.initial()
    assert status.up is False
    assert status.last_success_timestamp is None
    assert status.consecutive_failures == 0


def test_status_success_records_timestamp_and_resets_failures() -> None:
    status = ScrapeStatus.initial().failed(duration_seconds=0.5).failed(duration_seconds=0.5)
    assert status.consecutive_failures == 2
    status = status.succeeded(timestamp=123.0, duration_seconds=0.25)
    assert status.up is True
    assert status.last_success_timestamp == 123.0
    assert status.last_duration_seconds == 0.25
    assert status.consecutive_failures == 0


def test_status_failure_keeps_last_success_timestamp() -> None:
    status = ScrapeStatus.initial().succeeded(timestamp=123.0, duration_seconds=0.25)
    status = status.failed(duration_seconds=1.0)
    assert status.up is False
    assert status.last_success_timestamp == 123.0
    assert status.consecutive_failures == 1
