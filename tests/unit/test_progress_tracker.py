"""Unit tests for ProgressTracker."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from ingestion.shared.progress_tracker import ProgressTracker


class TestInit:
    """Verify constructor defaults and clamping."""

    def test_defaults(self) -> None:
        tracker = ProgressTracker(run_id="test-run", total_items=100)
        assert tracker.run_id == "test-run"
        assert tracker.total_items == 100
        assert tracker.log_every == 25
        assert tracker.log_interval_seconds == 30
        assert tracker.completed == 0
        assert tracker.succeeded == 0
        assert tracker.failed == 0
        assert tracker.sensors_with_data == 0

    def test_custom_values_clamped(self) -> None:
        tracker = ProgressTracker(
            run_id="test", total_items=100, log_every=0, log_interval_seconds=3
        )
        assert tracker.log_every == 1
        assert tracker.log_interval_seconds == 5


class TestLifecycle:
    """Verify start/stop lifecycle."""

    def test_start_creates_and_starts_thread(self) -> None:
        with patch("threading.Thread") as mock_thread_cls:
            tracker = ProgressTracker(run_id="test", total_items=100)
            tracker.start()
            mock_thread_cls.assert_called_once()
            mock_thread_cls.return_value.start.assert_called_once()

    def test_stop_joins_thread_and_logs_final(self) -> None:
        tracker = ProgressTracker(run_id="test", total_items=100)
        tracker._thread = MagicMock()
        with patch.object(tracker, "_log_snapshot") as mock_log:
            tracker.stop()
            tracker._thread.join.assert_called_once_with(timeout=2)
            mock_log.assert_called_once_with(force=True, reason="final")

    def test_stop_when_thread_is_none_does_not_raise(self) -> None:
        tracker = ProgressTracker(run_id="test", total_items=100)
        with patch.object(tracker, "_log_snapshot") as mock_log:
            tracker.stop()
            mock_log.assert_called_once_with(force=True, reason="final")


class TestRecordHttpAttempt:
    """Verify record_http_attempt increments the counter."""

    def test_increments_http_attempts(self) -> None:
        tracker = ProgressTracker(run_id="test", total_items=100)
        tracker.record_http_attempt()
        assert tracker.http_attempts == 1
        tracker.record_http_attempt()
        assert tracker.http_attempts == 2


class TestRecordRetry:
    """Verify record_retry increments the counter."""

    def test_increments_retries(self) -> None:
        tracker = ProgressTracker(run_id="test", total_items=100)
        tracker.record_retry()
        assert tracker.retries == 1


class TestRecordSuccess:
    """Verify record_success updates counters and optionally triggers log."""

    def test_had_data_true(self) -> None:
        tracker = ProgressTracker(run_id="test", total_items=100)
        tracker.record_success(rows_count=5, had_data=True)
        assert tracker.completed == 1
        assert tracker.succeeded == 1
        assert tracker.rows == 5
        assert tracker.sensors_with_data == 1

    def test_had_data_false(self) -> None:
        tracker = ProgressTracker(run_id="test", total_items=100)
        tracker.record_success(rows_count=0, had_data=False)
        assert tracker.completed == 1
        assert tracker.sensors_with_data == 0

    def test_triggers_log_at_threshold(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        with patch("ingestion.shared.progress_tracker.time.monotonic", return_value=0.0):
            tracker = ProgressTracker(run_id="test", total_items=100, log_every=5)
            for _ in range(5):
                tracker.record_success(rows_count=1, had_data=True)
        progress_logs = [r for r in caplog.records if "Poller progress" in r.message]
        assert len(progress_logs) >= 1


class TestRecordFailure:
    """Verify record_failure updates counters and optionally triggers log."""

    def test_increments_failed_and_completed(self) -> None:
        tracker = ProgressTracker(run_id="test", total_items=100)
        tracker.record_failure()
        assert tracker.completed == 1
        assert tracker.failed == 1
        assert tracker.succeeded == 0

    def test_triggers_log_at_threshold(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        with patch("ingestion.shared.progress_tracker.time.monotonic", return_value=0.0):
            tracker = ProgressTracker(run_id="test", total_items=100, log_every=5)
            for _ in range(5):
                tracker.record_failure()
        progress_logs = [r for r in caplog.records if "Poller progress" in r.message]
        assert len(progress_logs) >= 1


class TestSnapshotThrottling:
    """Verify _log_snapshot respects the throttle guard."""

    def test_no_log_below_threshold(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        tracker = ProgressTracker(run_id="test", total_items=100, log_every=25)
        tracker.completed = 10
        tracker._log_snapshot(force=False)
        assert "Poller progress" not in caplog.text

    def test_log_forced_with_force_flag(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        tracker = ProgressTracker(run_id="test", total_items=100, log_every=25)
        tracker.completed = 10
        tracker._log_snapshot(force=True)
        assert "Poller progress" in caplog.text


class TestSnapshotMath:
    """Verify snapshot computes correct ETA, rate, and percentage."""

    @staticmethod
    def _run_snapshot(
        tracker: ProgressTracker, elapsed: float
    ) -> dict[str, object]:
        """Run _log_snapshot with a given elapsed time and return parsed values."""
        with (
            patch("ingestion.shared.progress_tracker.time.monotonic", return_value=elapsed),
            patch(
                "ingestion.shared.progress_tracker.logging.Logger.info"
            ) as mock_log,
        ):
            tracker._log_snapshot(force=True, reason="test")
            call_args = mock_log.call_args[0]
            # The log message contains the values in positional args
            # return them as a dict for assertion
            return {
                "completed": call_args[3],
                "total": call_args[4],
                "pct": call_args[5],
                "succeeded": call_args[6],
                "failed": call_args[7],
                "with_data": call_args[8],
                "rows": call_args[9],
                "http_attempts": call_args[10],
                "retries": call_args[11],
                "rate": call_args[12],
                "elapsed": call_args[13],
                "eta": call_args[14],
            }

    def test_eta_zero_when_all_completed(self) -> None:
        tracker = ProgressTracker(run_id="test", total_items=100, log_every=25)
        tracker.completed = 100
        snap = self._run_snapshot(tracker, elapsed=60.0)
        assert snap["completed"] == 100
        assert snap["eta"] == "0s"

    def test_eta_positive_when_partial(self) -> None:
        with patch("ingestion.shared.progress_tracker.time.monotonic", return_value=0.0):
            tracker = ProgressTracker(run_id="test", total_items=100, log_every=25)
            for _ in range(50):
                tracker.record_success(rows_count=2, had_data=True)
        snap = self._run_snapshot(tracker, elapsed=60.0)
        assert snap["completed"] == 50
        assert snap["total"] == 100
        # pct = 50/100 * 100 = 50%
        assert snap["pct"] == 50.0
        # rate = (50 / 60) * 60 = 50.0 items/min
        assert snap["rate"] == 50.0
        # eta = (50 / 50) * 60 = 60.0s
        assert snap["eta"] == "60s"


class TestHeartbeat:
    """Verify the heartbeat loop fires _log_snapshot at interval."""

    def test_heartbeat_fires_log_snapshot(self) -> None:
        tracker = ProgressTracker(run_id="test", total_items=100, log_interval_seconds=30)
        call_count: list[int] = [0]

        def fake_wait(timeout: float) -> bool:
            call_count[0] += 1
            return call_count[0] > 1  # first: False (keep looping), second: True (exit)

        with patch.object(tracker, "_log_snapshot") as mock_log:
            with patch.object(tracker._stop_event, "wait", fake_wait):
                tracker._heartbeat_loop()
        mock_log.assert_called_once_with(force=True, reason="heartbeat")
