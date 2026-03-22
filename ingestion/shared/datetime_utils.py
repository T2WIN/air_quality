"""Shared datetime utilities for ingestion pollers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def to_rfc3339_z(dt: datetime) -> str:
    """Convert datetime to RFC3339 string with Z suffix for UTC.

    Converts +00:00 timezone offset to 'Z' for consistency.
    Example: 2024-01-15T10:30:00+00:00 -> 2024-01-15T10:30:00Z
    """
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse an RFC3339 or ISO format timestamp string to datetime."""
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def build_run_id(run_started_at: datetime) -> str:
    """Generate a unique run ID from the start time and a short UUID.

    Format: YYYYMMDDTHHMMSSZ_<uuid8chars>
    Example: 20240115T103000Z_a1b2c3d4
    """
    return f"{run_started_at.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"


def deep_get(obj: dict[str, Any], *keys: str) -> Any:
    """Safely navigate nested dicts and return None if any key is missing."""
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


# ---------------------------------------------------------------------------
# Env-var helpers
# ---------------------------------------------------------------------------


def parse_csv_env(raw: str | None) -> list[str]:
    """Parse a comma-separated environment variable into a list of strings."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_optional_int(value: Optional[str]) -> Optional[int]:
    """Parse an optional integer from a string."""
    if value is None or value == "":
        return None
    return int(value)
