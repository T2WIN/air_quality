"""Thread-safe aggregated progress tracker for long-running polling jobs."""

import logging
import threading
import time
from typing import Optional


class ProgressTracker:
    """Logs a snapshot every *log_every* completed sensors **or** every
    *log_interval_seconds* via a background heartbeat thread — whichever
    comes first.  A final snapshot is always emitted when ``stop()`` is
    called.

    All public ``record_*`` methods are safe to call from any thread.
    """

    def __init__(
        self,
        run_id: str,
        total_sensors: int,
        log_every: int = 25,
        log_interval_seconds: int = 30,
    ):
        self.run_id = run_id
        self.total_sensors = total_sensors
        self.log_every = max(1, log_every)
        self.log_interval_seconds = max(5, log_interval_seconds)

        self.started_at = time.monotonic()

        # Counters — only mutated under _lock
        self.completed = 0
        self.succeeded = 0
        self.failed = 0
        self.sensors_with_data = 0
        self.rows = 0
        self.http_attempts = 0
        self.retries = 0

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Dedup / throttle guards
        self._last_logged_completed = 0
        self._last_logged_at = self.started_at

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start the background heartbeat thread."""
        logging.info(
            "Progress tracker started  run_id=%s  total_sensors=%s  "
            "log_every=%s  log_interval=%ss",
            self.run_id,
            self.total_sensors,
            self.log_every,
            self.log_interval_seconds,
        )
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"progress-{self.run_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the heartbeat thread and emit a final snapshot."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._log_snapshot(force=True, reason="final")

    # -- recording (called from worker threads) ------------------------------

    def record_http_attempt(self) -> None:
        with self._lock:
            self.http_attempts += 1

    def record_retry(self) -> None:
        with self._lock:
            self.retries += 1

    def record_success(self, rows_count: int, had_data: bool) -> None:
        should_log = False
        with self._lock:
            self.completed += 1
            self.succeeded += 1
            self.rows += rows_count
            if had_data:
                self.sensors_with_data += 1
            should_log = (
                self._should_log_locked(time.monotonic())
                or self.completed == self.total_sensors
            )
        if should_log:
            self._log_snapshot(reason="progress")

    def record_failure(self) -> None:
        should_log = False
        with self._lock:
            self.completed += 1
            self.failed += 1
            should_log = (
                self._should_log_locked(time.monotonic())
                or self.completed == self.total_sensors
            )
        if should_log:
            self._log_snapshot(reason="progress")

    # -- internal ------------------------------------------------------------

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(self.log_interval_seconds):
            self._log_snapshot(force=True, reason="heartbeat")

    def _should_log_locked(self, now: float) -> bool:
        """Must be called while holding ``self._lock``."""
        return (
            self.completed - self._last_logged_completed >= self.log_every
            or now - self._last_logged_at >= self.log_interval_seconds
        )

    def _log_snapshot(
        self, force: bool = False, reason: str = "progress"
    ) -> None:
        # --- capture values under lock ---
        with self._lock:
            now = time.monotonic()

            if (
                not force
                and not self._should_log_locked(now)
                and self.completed != self.total_sensors
            ):
                return

            elapsed = max(0.001, now - self.started_at)
            remaining = max(0, self.total_sensors - self.completed)
            rate = (self.completed / elapsed) * 60.0
            eta = (
                (remaining / self.completed) * elapsed
                if self.completed > 0
                else None
            )

            snap = {
                "completed": self.completed,
                "total": self.total_sensors,
                "succeeded": self.succeeded,
                "failed": self.failed,
                "with_data": self.sensors_with_data,
                "rows": self.rows,
                "http_attempts": self.http_attempts,
                "retries": self.retries,
                "rate": rate,
                "elapsed": elapsed,
                "eta": eta,
            }

            self._last_logged_completed = self.completed
            self._last_logged_at = now

        # --- log outside the lock ---
        pct = (
            snap["completed"] / snap["total"] * 100.0
            if snap["total"]
            else 100.0
        )
        eta_display = (
            f'{snap["eta"]:.0f}s' if snap["eta"] is not None else "unknown"
        )

        logging.info(
            "Poller progress  run_id=%s  reason=%s  "
            "completed=%s/%s (%.1f%%)  "
            "success=%s  failed=%s  with_data=%s  rows=%s  "
            "http_attempts=%s  retries=%s  "
            "rate=%.1f sensors/min  elapsed=%.0fs  eta=%s",
            self.run_id,
            reason,
            snap["completed"],
            snap["total"],
            pct,
            snap["succeeded"],
            snap["failed"],
            snap["with_data"],
            snap["rows"],
            snap["http_attempts"],
            snap["retries"],
            snap["rate"],
            snap["elapsed"],
            eta_display,
        )