import threading
from datetime import timezone
from email.utils import parsedate_to_datetime
import random
import requests
from .datetime_utils import utc_now

_thread_local = threading.local()


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


def get_session(
    *,
    pool_connections: int = 10,
    pool_maxsize: int = 10,
    extra_headers: dict[str, str] | None = None,
) -> requests.Session:
    """Get or create a thread-local requests session.

    Args:
        pool_connections: Number of connection pools to cache.
        pool_maxsize: Maximum number of connections per pool.
        extra_headers: Additional headers to set on the session.
    """
    attr_name = f"_session_{pool_connections}_{pool_maxsize}"
    if not hasattr(_thread_local, attr_name):
        session = requests.Session()

        adapter = requests.adapters.HTTPAdapter(
            max_retries=0,
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        headers: dict[str, str] = {"Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        session.headers.update(headers)

        setattr(_thread_local, attr_name, session)

    return getattr(_thread_local, attr_name)


# ---------------------------------------------------------------------------
# Retry / back-off helpers
# ---------------------------------------------------------------------------


def _parse_retry_after(value: str | None) -> float | None:
    """Parse an HTTP ``Retry-After`` header (seconds or HTTP-date)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(
            0.0, (dt.astimezone(timezone.utc) - utc_now()).total_seconds()
        )
    except Exception:
        return None


def backoff_seconds(
    attempt: int, response: requests.Response | None = None
) -> float:
    """Exponential back-off with jitter, honouring Retry-After if present."""
    if response is not None:
        retry_after = _parse_retry_after(
            response.headers.get("Retry-After")
        )
        if retry_after is not None:
            return retry_after
    base = min(2 ** (attempt - 1), 30)
    return base + random.uniform(0, 0.5)
