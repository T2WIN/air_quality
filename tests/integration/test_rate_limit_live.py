"""Live integration test for DualWindowRateLimiter against the OpenAQ API.

Spawns multiple threads that issue paginated requests through the rate limiter
and reports whether any 429 responses occur.

Usage (see run_rate_limit_test.sh for the canonical wrapper):

    OPENAQ_API_KEY=... OPENAQ_RATE_LIMIT_PER_MINUTE=40 OPENAQ_RATE_LIMIT_PER_HOUR=1000 \
        python -m tests.integration.test_rate_limit_live
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import Counter
from collections.abc import Hashable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import requests
from google.cloud import secretmanager

# Allow running from project root without installation
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ingestion.shared import DualWindowRateLimiter

# ---------------------------------------------------------------------------
# Secret Manager fallback
# ---------------------------------------------------------------------------


def _fetch_secret(project_id: str, secret_id: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


def _resolve_api_key() -> str:
    key = os.getenv("OPENAQ_API_KEY")
    if key:
        return key
    project_id = os.getenv("PROJECT_ID")
    if not project_id:
        return ""
    try:
        print(f"OPENAQ_API_KEY not set — fetching from Secret Manager ({project_id})...")
        return _fetch_secret(project_id, "OPENAQ_API_KEY")
    except Exception as exc:
        print(f"WARNING: Could not fetch secret: {exc}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

THREAD_COUNT = 8
REQUESTS_PER_THREAD = 9  # 8 * 9 = 72 total requests
BASE_URL = os.getenv("OPENAQ_BASE_URL", "https://api.openaq.org/v3").rstrip("/")
API_KEY = _resolve_api_key()
RATE_PER_MINUTE = int(os.getenv("OPENAQ_RATE_LIMIT_PER_MINUTE", "40"))
RATE_PER_HOUR = int(os.getenv("OPENAQ_RATE_LIMIT_PER_HOUR", "1000"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RequestRecord:
    """Single request observation."""

    thread_id: int
    page: int
    monotonic_start: float
    monotonic_end: float
    status_code: int | None
    elapsed: float
    error: str | None = None


@dataclass
class TestReport:
    """Aggregated results from all threads."""

    records: list[RequestRecord] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, record: RequestRecord) -> None:
        with self.lock:
            self.records.append(record)

    def summary(self) -> dict[str, Any]:
        total = len(self.records)
        status_counter = Counter(r.status_code for r in self.records)
        count_429 = status_counter.get(429, 0)
        other_errors = sum(
            v for k, v in status_counter.items() if k is None or (k >= 400 and k != 429)
        )

        # Compute max requests in any 60s sliding window
        max_per_min = _max_requests_in_window(60.0)

        # Longest gap between consecutive requests (evidence of throttling)
        longest_gap = _longest_gap()

        # Timeline: requests per 10s bucket
        timeline = _timeline_buckets(10.0)

        # Distinct status codes with counts
        status_breakdown = {
            str(k) if k is not None else "error": v for k, v in sorted(status_counter.items())
        }

        return {
            "total_requests": total,
            "status_200": status_counter.get(200, 0),
            "status_429": count_429,
            "other_errors": other_errors,
            "max_req_per_min": max_per_min,
            "longest_gap_s": longest_gap,
            "timeline_10s": timeline,
            "status_breakdown": status_breakdown,
        }


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------


def _max_requests_in_window(window: float) -> int:
    """Return the maximum number of requests observed in any *window*-second
    sliding window across all recorded requests."""
    report = _global_report
    if not report.records:
        return 0
    timestamps = sorted(r.monotonic_start for r in report.records)
    max_count = 0
    j = 0
    for i in range(len(timestamps)):
        while timestamps[i] - timestamps[j] >= window:
            j += 1
        max_count = max(max_count, i - j + 1)
    return max_count


def _longest_gap() -> float:
    """Return the longest gap (seconds) between consecutive requests."""
    report = _global_report
    if len(report.records) < 2:
        return 0.0
    timestamps = sorted(r.monotonic_start for r in report.records)
    gaps = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
    return max(gaps) if gaps else 0.0


def _timeline_buckets(bucket_size: float) -> list[tuple[float, int]]:
    """Return a list of (bucket_start, count) for *bucket_size*-second
    intervals from the first request to the last."""
    report = _global_report
    if not report.records:
        return []
    timestamps = sorted(r.monotonic_start for r in report.records)
    t_min = timestamps[0]
    t_max = timestamps[-1]
    if t_max == t_min:
        return [(t_min, len(timestamps))]

    buckets: Counter[int] = Counter()
    for t in timestamps:
        bucket_idx = int((t - t_min) / bucket_size)
        buckets[bucket_idx] += 1

    n_buckets = int((t_max - t_min) / bucket_size) + 1
    return [(t_min + i * bucket_size, buckets.get(i, 0)) for i in range(n_buckets)]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _worker(
    thread_id: int,
    rate_limiter: DualWindowRateLimiter,
    session: requests.Session,
) -> list[RequestRecord]:
    """Make REQUESTS_PER_THREAD paginated requests through *rate_limiter*."""
    records: list[RequestRecord] = []
    for page in range(1, REQUESTS_PER_THREAD + 1):
        wait_start = time.monotonic()
        rate_limiter.acquire()
        t0 = time.monotonic()
        try:
            resp = session.get(
                f"{BASE_URL}/countries",
                params={"limit": 1, "page": page},
                timeout=HTTP_TIMEOUT,
            )
            elapsed = time.monotonic() - wait_start
            records.append(
                RequestRecord(
                    thread_id=thread_id,
                    page=page,
                    monotonic_start=t0,
                    monotonic_end=time.monotonic(),
                    status_code=resp.status_code,
                    elapsed=elapsed,
                )
            )
        except Exception as exc:
            elapsed = time.monotonic() - wait_start
            records.append(
                RequestRecord(
                    thread_id=thread_id,
                    page=page,
                    monotonic_start=t0,
                    monotonic_end=time.monotonic(),
                    status_code=None,
                    elapsed=elapsed,
                    error=str(exc),
                )
            )
    return records


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_global_report = TestReport()


def _print_report(report: TestReport) -> None:
    s = report.summary()
    sep = "=" * 60

    print(sep)
    print("  RATE LIMITER INTEGRATION TEST REPORT")
    print(sep)
    print(f"  Total requests : {s['total_requests']}")
    print(f"  HTTP 200       : {s['status_200']}")
    print(f"  HTTP 429       : {s['status_429']}")
    print(f"  Other errors   : {s['other_errors']}")
    print(f"  Max req/min    : {s['max_req_per_min']} (limit: {RATE_PER_MINUTE})")
    print(f"  Longest gap    : {s['longest_gap_s']:.2f}s")
    print(sep)
    print("  STATUS CODE BREAKDOWN")
    print(sep)
    for code, count in s["status_breakdown"].items():
        print(f"  {code:>8s} : {count}")
    print(sep)
    print("  TIMELINE (requests per 10s bucket)")
    print(sep)

    for bucket_start, count in s["timeline_10s"]:
        bar = "#" * count
        print(f"  +{bucket_start:7.1f}s | {bar} ({count})")

    print(sep)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not API_KEY:
        print("ERROR: OPENAQ_API_KEY is not set", file=sys.stderr)
        return 1

    print(f"Config: threads={THREAD_COUNT}, requests/thread={REQUESTS_PER_THREAD}")
    print(f"        rate_limit={RATE_PER_MINUTE}/min {RATE_PER_HOUR}/hour")
    print(f"        base_url={BASE_URL}")
    print()

    rate_limiter = DualWindowRateLimiter(
        per_minute=RATE_PER_MINUTE,
        per_hour=RATE_PER_HOUR,
    )
    session = requests.Session()
    session.headers.update({"X-API-Key": API_KEY})

    t_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=THREAD_COUNT) as executor:
        futures = {
            executor.submit(_worker, tid, rate_limiter, session): tid for tid in range(THREAD_COUNT)
        }
        for future in as_completed(futures):
            tid = futures[future]
            records = future.result()
            for r in records:
                _global_report.add(r)
            print(f"  Thread {tid} completed {len(records)} requests")

    elapsed_total = time.monotonic() - t_start
    print(f"\nAll threads finished in {elapsed_total:.1f}s")
    print()

    _print_report(_global_report)

    has_429 = any(r.status_code == 429 for r in _global_report.records)
    has_errors = any(
        r.status_code is not None and r.status_code >= 400 for r in _global_report.records
    )

    if has_429:
        print("\nFAILED: Received HTTP 429 responses — rate limiter did not prevent throttling")
        return 1
    if has_errors:
        print("\nWARNING: Non-429 HTTP errors occurred (see report above)")
        return 0
    print("\nPASSED: Zero 429 responses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
