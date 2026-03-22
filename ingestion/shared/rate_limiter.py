"""Thread-safe dual-window sliding rate limiter.

Enforces two concurrent sliding-window ceilings (per-minute and per-hour).
``acquire()`` blocks the calling thread until a request slot is available
under **both** windows.

Supports ``count > 1`` so a single multi-location API call can consume
multiple tokens (Open-Meteo counts each coordinate as one API call).
"""

from __future__ import annotations

import threading
import time
from collections import deque


class DualWindowRateLimiter:
    """Thread-safe dual-window sliding rate limiter."""

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

    def acquire(self, count: int = 1) -> None:
        """Block until *count* request slots are available, then reserve them.

        Parameters
        ----------
        count:
            Number of tokens to consume.  For Open-Meteo, pass the number
            of coordinates in the batch (each coordinate = 1 API call).

        Raises
        ------
        ValueError:
            If *count* exceeds either window's capacity (would block forever).
        """
        if count < 1:
            raise ValueError(f"count must be >= 1, got {count}")
        if count > self.per_minute:
            raise ValueError(
                f"count={count} exceeds per_minute limit={self.per_minute} "
                f"(would block forever)"
            )
        if count > self.per_hour:
            raise ValueError(
                f"count={count} exceeds per_hour limit={self.per_hour} "
                f"(would block forever)"
            )

        while True:
            with self._lock:
                now = time.monotonic()
                self._evict_old(now)

                minute_ok = len(self._minute_calls) + count <= self.per_minute
                hour_ok = len(self._hour_calls) + count <= self.per_hour

                if minute_ok and hour_ok:
                    for _ in range(count):
                        self._minute_calls.append(now)
                        self._hour_calls.append(now)
                    return

                # Calculate how long to wait for enough slots to free up
                wait_minute = 0.0
                wait_hour = 0.0

                if not minute_ok:
                    # We need to free up enough entries so that
                    # len(deque) + count <= per_minute.
                    # The number of entries that must expire:
                    need_to_free = (
                        len(self._minute_calls) + count - self.per_minute
                    )
                    # Wait until the need_to_free-th oldest entry expires
                    idx = need_to_free - 1
                    if idx < len(self._minute_calls):
                        wait_minute = self._minute_window - (
                            now - self._minute_calls[idx]
                        )

                if not hour_ok:
                    need_to_free = (
                        len(self._hour_calls) + count - self.per_hour
                    )
                    idx = need_to_free - 1
                    if idx < len(self._hour_calls):
                        wait_hour = self._hour_window - (
                            now - self._hour_calls[idx]
                        )

                sleep_for = max(wait_minute, wait_hour, 0.05)

            time.sleep(sleep_for)