"""Unit tests for ingestion.shared.datetime_utils."""

from __future__ import annotations

from unittest.mock import patch

from ingestion.shared.datetime_utils import (
    build_run_id,
    deep_get,
    parse_csv_env,
    parse_optional_int,
    parse_timestamp,
    to_rfc3339_z,
    utc_now,
)


class TestUtcNow:
    """Verify utc_now returns a timezone-aware UTC datetime."""

    def test_utc_now_has_utc_tz(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None
        assert str(now.tzinfo) == "UTC"


class TestToRfc3339Z:
    """Verify to_rfc3339_z produces correct RFC3339 strings."""

    def test_utc_datetime_gets_z_suffix(self) -> None:
        from datetime import UTC, datetime

        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = to_rfc3339_z(dt)
        assert result == "2024-01-15T10:30:00Z"

    def test_non_utc_offset_converted_to_z(self) -> None:
        import zoneinfo
        from datetime import datetime

        dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=zoneinfo.ZoneInfo("Europe/Paris"))
        result = to_rfc3339_z(dt)
        # Paris is UTC+2 in June; converted to UTC it should be 10:00Z
        assert result == "2024-06-15T10:00:00Z"


class TestParseTimestamp:
    """Verify parse_timestamp handles various ISO/RFC3339 inputs."""

    def test_z_suffix(self) -> None:
        result = parse_timestamp("2024-01-15T10:30:00Z")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30
        assert result.second == 0
        assert str(result.tzinfo) == "UTC"

    def test_plus_00_00_offset(self) -> None:
        result = parse_timestamp("2024-01-15T10:30:00+00:00")
        assert result is not None
        assert result.hour == 10
        assert str(result.tzinfo) == "UTC"

    def test_none_returns_none(self) -> None:
        assert parse_timestamp(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_timestamp("") is None


class TestBuildRunId:
    """Verify build_run_id produces the expected format."""

    def test_format(self) -> None:
        from datetime import UTC, datetime

        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        with patch("ingestion.shared.datetime_utils.uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "a1b2c3d4e5f6"
            result = build_run_id(dt)
        assert result == "20240115T103000Z_a1b2c3d4"
        assert len(result.split("_")[1]) == 8


class TestDeepGet:
    """Verify deep_get safely navigates nested dicts."""

    def test_simple_key(self) -> None:
        assert deep_get({"a": 1}, "a") == 1

    def test_nested_keys(self) -> None:
        assert deep_get({"a": {"b": {"c": 42}}}, "a", "b", "c") == 42

    def test_missing_key_returns_none(self) -> None:
        assert deep_get({"a": 1}, "b") is None

    def test_non_dict_intermediate_returns_none(self) -> None:
        assert deep_get({"a": "not_a_dict"}, "a", "b") is None

    def test_none_value_returns_none(self) -> None:
        assert deep_get({"a": {"b": None}}, "a", "b") is None


class TestParseCsvEnv:
    """Verify parse_csv_env handles env-var parsing."""

    def test_valid_csv(self) -> None:
        assert parse_csv_env("a, b, c") == ["a", "b", "c"]

    def test_none_returns_empty_list(self) -> None:
        assert parse_csv_env(None) == []

    def test_empty_string_returns_empty_list(self) -> None:
        assert parse_csv_env("") == []

    def test_whitespace_items_are_skipped(self) -> None:
        assert parse_csv_env("a, , b,   ") == ["a", "b"]


class TestParseOptionalInt:
    """Verify parse_optional_int handles optional integer parsing."""

    def test_valid_int_string(self) -> None:
        assert parse_optional_int("42") == 42

    def test_none_returns_none(self) -> None:
        assert parse_optional_int(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_optional_int("") is None
