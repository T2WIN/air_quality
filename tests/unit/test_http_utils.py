"""Unit tests for http_utils — session factory and retry backoff."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from ingestion.shared import http_utils


class TestGetSession:
    """get_session() — thread-local requests session factory."""

    def teardown_method(self) -> None:
        for attr in list(vars(http_utils._thread_local)):
            delattr(http_utils._thread_local, attr)

    def test_default_args(self) -> None:
        session = http_utils.get_session()
        assert session.headers["Accept"] == "application/json"

    def test_extra_headers(self) -> None:
        session = http_utils.get_session(extra_headers={"X-API-Key": "test123"})
        assert session.headers["Accept"] == "application/json"
        assert session.headers["X-API-Key"] == "test123"

    def test_same_thread_reuse(self) -> None:
        s1 = http_utils.get_session()
        s2 = http_utils.get_session()
        assert s1 is s2

    def test_different_thread_fresh(self) -> None:
        sessions: list[requests.Session] = []

        def get_from_thread() -> None:
            sessions.append(http_utils.get_session())

        s_main = http_utils.get_session()
        t = threading.Thread(target=get_from_thread)
        t.start()
        t.join()
        assert s_main is not sessions[0]


class TestParseRetryAfter:
    """_parse_retry_after() — parse Retry-After header (seconds or HTTP-date)."""

    def test_none_or_empty_returns_none(self) -> None:
        assert http_utils._parse_retry_after(None) is None
        assert http_utils._parse_retry_after("") is None

    def test_seconds_value(self) -> None:
        assert http_utils._parse_retry_after("30") == 30.0

    def test_seconds_value_clamps_negative(self) -> None:
        assert http_utils._parse_retry_after("-5") == 0.0

    def test_http_date(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        with patch.object(http_utils, "utc_now", return_value=now):
            result = http_utils._parse_retry_after("Wed, 01 Jan 2025 12:01:00 GMT")
            assert result == pytest.approx(60.0, abs=1)

    def test_invalid_value_returns_none(self) -> None:
        assert http_utils._parse_retry_after("not-a-date") is None


class TestBackoffSeconds:
    """backoff_seconds() — exponential backoff + jitter, honouring Retry-After."""

    def test_no_response_exponential_backoff(self) -> None:
        with patch.object(http_utils.random, "uniform", return_value=0.0):
            assert http_utils.backoff_seconds(1) == 1.0
            assert http_utils.backoff_seconds(2) == 2.0
            assert http_utils.backoff_seconds(3) == 4.0
            assert http_utils.backoff_seconds(5) == 16.0
            assert http_utils.backoff_seconds(10) == 30.0

    def test_retry_after_seconds(self) -> None:
        response = MagicMock(spec=requests.Response)
        response.headers = {"Retry-After": "15"}
        assert http_utils.backoff_seconds(3, response) == 15.0

    def test_retry_after_http_date(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        with patch.object(http_utils, "utc_now", return_value=now):
            response = MagicMock(spec=requests.Response)
            response.headers = {"Retry-After": "Wed, 01 Jan 2025 12:05:00 GMT"}
            result = http_utils.backoff_seconds(2, response)
            assert result == pytest.approx(300.0, abs=1)

    def test_invalid_retry_after_falls_through(self) -> None:
        with patch.object(http_utils.random, "uniform", return_value=0.0):
            response = MagicMock(spec=requests.Response)
            response.headers = {"Retry-After": "garbage"}
            assert http_utils.backoff_seconds(4, response) == 8.0
