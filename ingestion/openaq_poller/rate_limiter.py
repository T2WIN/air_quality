"""Thread-safe dual-window sliding rate limiter."""

import threading
import time
from collections import deque


class DualWindowRateLimiter:
    """Enforces two concurrent sliding-window ceilings (per-minute and
    per-hour).  ``acquire()`` blocks the calling thread until a request
    slot is available under **both** windows.
    """

    def __init__(self, per_minute: int, per_hour: int):
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._minute_window = 60.0
        self._hour_window = 3600.0
        self._minute_calls: deque[float] = deque()
        self._hour_calls: deque[float] = deque()
        self._lock = threading.Lock()

    def _evict_old(self, now: float) -> None:
        while (
            self._minute_calls
            and now - self._minute_calls[0] >= self._minute_window
        ):
            self._minute_calls.popleft()
        while (
            self._hour_calls
            and now - self._hour_calls[0] >= self._hour_window
        ):
            self._hour_calls.popleft()

    def acquire(self) -> None:
        """Block until one request slot is available, then reserve it."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._evict_old(now)

                minute_ok = len(self._minute_calls) < self.per_minute
                hour_ok = len(self._hour_calls) < self.per_hour

                if minute_ok and hour_ok:
                    self._minute_calls.append(now)
                    self._hour_calls.append(now)
                    return

                wait_minute = 0.0
                wait_hour = 0.0

                if not minute_ok and self._minute_calls:
                    wait_minute = self._minute_window - (
                        now - self._minute_calls[0]
                    )
                if not hour_ok and self._hour_calls:
                    wait_hour = self._hour_window - (
                        now - self._hour_calls[0]
                    )

                sleep_for = max(wait_minute, wait_hour, 0.05)

            time.sleep(sleep_for)