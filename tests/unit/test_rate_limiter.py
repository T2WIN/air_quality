"""Unit tests for DualWindowRateLimiter."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from ingestion.shared.rate_limiter import DualWindowRateLimiter


class TestAcquireWithinMinuteLimit:
    """Verify that up to per_minute acquires succeed immediately."""

    def test_acquire_within_minute_limit(self) -> None:
        limiter = DualWindowRateLimiter(per_minute=40, per_hour=1000)
        for _ in range(40):
            limiter.acquire()  # should not block


class TestBlocksAtMinuteLimit:
    """Verify the 41st acquire within 60 seconds blocks."""

    def test_acquire_blocks_at_minute_limit(self) -> None:
        limiter = DualWindowRateLimiter(per_minute=40, per_hour=1000)
        for _ in range(40):
            limiter.acquire()

        # The 41st call should block — we verify it doesn't return immediately
        result: list[float] = []

        def try_acquire() -> None:
            start = time.monotonic()
            limiter.acquire()
            result.append(time.monotonic() - start)

        t = threading.Thread(target=try_acquire)
        t.start()
        t.join(timeout=0.2)
        # Thread should still be alive (blocked waiting for a slot)
        assert t.is_alive(), "41st acquire returned immediately — should have blocked"
        # We don't want to wait 60s, so we just verify it blocked


class TestHourWindowEnforcement:
    """Verify hour window limits are respected via mocked time."""

    def test_acquire_within_hour_limit(self) -> None:
        limiter = DualWindowRateLimiter(per_minute=100, per_hour=50)
        # Fill up the hour window
        for _ in range(50):
            limiter.acquire()

        # 51st should block since hour window is full
        blocked = threading.Event()

        def try_acquire() -> None:
            limiter.acquire()
            blocked.set()

        t = threading.Thread(target=try_acquire)
        t.start()
        t.join(timeout=0.2)
        assert not blocked.is_set(), "51st acquire should have blocked (hour limit)"


class TestSlidingWindowEviction:
    """Verify old entries are evicted after window expires."""

    def test_sliding_window_eviction(self) -> None:
        limiter = DualWindowRateLimiter(per_minute=5, per_hour=1000)
        current_time: list[float] = [0.0]
        sleep_requested: list[float] = []

        def fake_monotonic() -> float:
            return current_time[0]

        def fake_sleep(duration: float) -> None:
            sleep_requested.append(duration)
            # Advance time by the sleep duration to simulate time passing
            current_time[0] += duration

        with (
            patch("ingestion.shared.rate_limiter.time.monotonic", fake_monotonic),
            patch("ingestion.shared.rate_limiter.time.sleep", fake_sleep),
        ):
            # Fill the minute window
            for _ in range(5):
                limiter.acquire()

            # 6th should block initially, then succeed after time advances
            limiter.acquire()

            # Verify a sleep was requested (blocking behavior)
            assert len(sleep_requested) >= 1, "Expected at least one sleep call"
            # The sleep duration should be close to 60s (full window)
            assert sleep_requested[0] >= 59.0, (
                f"Expected ~60s wait, got {sleep_requested[0]}"
            )


class TestCountGreaterThanOne:
    """Verify acquire(count=N) consumes N tokens."""

    def test_count_greater_than_one(self) -> None:
        limiter = DualWindowRateLimiter(per_minute=10, per_hour=1000)

        # Acquire 5 tokens at once
        limiter.acquire(count=5)

        # Should have 5 remaining
        for _ in range(5):
            limiter.acquire()

        # 11th token should block
        blocked = threading.Event()

        def try_acquire() -> None:
            limiter.acquire()
            blocked.set()

        t = threading.Thread(target=try_acquire)
        t.start()
        t.join(timeout=0.2)
        assert not blocked.is_set(), "Should be blocked after consuming all 10 tokens"


class TestCountExceedsCapacity:
    """Verify acquire raises ValueError when count exceeds capacity."""

    def test_count_exceeds_capacity_raises(self) -> None:
        limiter = DualWindowRateLimiter(per_minute=40, per_hour=1000)

        with pytest.raises(ValueError, match="exceeds per_minute limit"):
            limiter.acquire(count=41)

    def test_count_exceeds_hour_capacity_raises(self) -> None:
        limiter = DualWindowRateLimiter(per_minute=100, per_hour=50)

        with pytest.raises(ValueError, match="exceeds per_hour limit"):
            limiter.acquire(count=51)

    def test_count_zero_raises(self) -> None:
        limiter = DualWindowRateLimiter(per_minute=40, per_hour=1000)

        with pytest.raises(ValueError, match="count must be >= 1"):
            limiter.acquire(count=0)


class TestConcurrentAccess:
    """Verify concurrent threads respect rate limits."""

    def test_concurrent_access_respects_limits(self) -> None:
        limiter = DualWindowRateLimiter(per_minute=10, per_hour=1000)
        num_threads = 8
        acquires_per_thread = 3  # 24 total, but only 10 should succeed immediately
        errors: list[Exception] = []
        acquire_times: list[float] = []
        lock = threading.Lock()
        current_time: list[float] = [0.0]

        def fake_monotonic() -> float:
            return current_time[0]

        def fake_sleep(duration: float) -> None:
            current_time[0] += duration

        def worker() -> None:
            try:
                for _ in range(acquires_per_thread):
                    start = fake_monotonic()
                    limiter.acquire()
                    elapsed = fake_monotonic() - start
                    with lock:
                        acquire_times.append(elapsed)
            except Exception as e:
                with lock:
                    errors.append(e)

        with (
            patch("ingestion.shared.rate_limiter.time.monotonic", fake_monotonic),
            patch("ingestion.shared.rate_limiter.time.sleep", fake_sleep),
        ):
            threads = [threading.Thread(target=worker) for _ in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

        assert not errors, f"Unexpected errors: {errors}"
        assert len(acquire_times) == num_threads * acquires_per_thread, (
            f"Expected {num_threads * acquires_per_thread} acquires, got {len(acquire_times)}"
        )
        # Some acquires should have had to wait (elapsed > 0 due to sleep)
        waited_count = sum(1 for elapsed in acquire_times if elapsed > 0.0)
        assert waited_count > 0, (
            f"Expected some acquires to wait, but all returned immediately. "
            f"Times: {acquire_times}"
        )


class TestWaitCalculation:
    """Verify wait duration is calculated correctly for the minute window."""

    def test_wait_calculation_for_minute_window(self) -> None:
        limiter = DualWindowRateLimiter(per_minute=5, per_hour=1000)
        current_time: list[float] = [0.0]
        sleep_durations: list[float] = []

        def fake_monotonic() -> float:
            return current_time[0]

        def capture_sleep(duration: float) -> None:
            sleep_durations.append(duration)
            # Advance time so the next iteration can succeed
            current_time[0] += duration

        with (
            patch("ingestion.shared.rate_limiter.time.monotonic", fake_monotonic),
            patch("ingestion.shared.rate_limiter.time.sleep", capture_sleep),
        ):
            # Fill the window
            for _ in range(5):
                limiter.acquire()

            # Next acquire should calculate a wait time and then succeed
            limiter.acquire()

        assert len(sleep_durations) >= 1, "Expected at least one sleep call"
        # The wait should be close to the full minute window since all entries
        # were recorded at time 0
        assert sleep_durations[0] >= 59.0, (
            f"Expected ~60s wait, got {sleep_durations[0]}"
        )
