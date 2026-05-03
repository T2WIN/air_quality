"""Unit tests for ingestion.openaq_poller.main."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from google.cloud import bigquery

from ingestion.openaq_poller.main import (
    Config,
    HourPayload,
    HourRow,
    PollingResult,
    Sensor,
    _append_rows_to_bigquery,
    _build_summary,
    _collect_future_result,
    _determine_status,
    _fetch_sensor_hours,
    _log_run,
    _parse_bool_env,
    _persist_rows,
    _transform_hour_row,
    get_query_window,
    load_station_sensors,
    run_poller,
)


# ---------------------------------------------------------------------------
# _parse_bool_env
# ---------------------------------------------------------------------------


class TestParseBoolEnv:
    def test_none_returns_default(self) -> None:
        assert _parse_bool_env(None, True) is True
        assert _parse_bool_env(None, False) is False

    def test_true_values(self) -> None:
        for val in ("1", "true", "TRUE", "t", "yes", "y"):
            assert _parse_bool_env(val, False) is True

    def test_false_values(self) -> None:
        assert _parse_bool_env("0", True) is False
        assert _parse_bool_env("false", True) is False
        assert _parse_bool_env("no", True) is False

    def test_whitespace_is_stripped(self) -> None:
        assert _parse_bool_env(" true ", False) is True
        assert _parse_bool_env(" 1 ", False) is True
        assert _parse_bool_env("  false  ", True) is False


# ---------------------------------------------------------------------------
# Config.from_env
# ---------------------------------------------------------------------------


class TestConfigFromEnv:
    def test_basic_config(self) -> None:
        env = {
            "PROJECT_ID": "my-project",
            "BQ_RAW_DATASET": "raw",
            "BQ_STATION_SENSORS_TABLE": "sensors",
            "BQ_OPENAQ_HOURLY_TABLE": "hourly",
            "BQ_LOCATION": "US",
            "OPENAQ_BASE_URL": "https://custom.openaq.org/v3",
            "OPENAQ_API_KEY": "key123",
            "LOOKBACK_HOURS": "6",
            "MAX_WORKERS": "4",
            "HTTP_TIMEOUT_SECONDS": "15",
            "DEV_STATION_IDS": "101, 102",
            "MAX_SENSORS": "50",
            "ENFORCE_COMPLETE_HOURS": "false",
            "TARGET_POLLUTANTS": "o3,pm10",
            "OPENAQ_RATE_LIMIT_PER_MINUTE": "30",
            "OPENAQ_RATE_LIMIT_PER_HOUR": "1000",
            "MAX_HTTP_ATTEMPTS": "3",
            "PROGRESS_LOG_EVERY": "10",
            "PROGRESS_LOG_INTERVAL_SECONDS": "15",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
        assert cfg.project_id == "my-project"
        assert cfg.raw_dataset == "raw"
        assert cfg.station_sensors_table == "sensors"
        assert cfg.hourly_table == "hourly"
        assert cfg.bq_location == "US"
        assert cfg.openaq_base_url == "https://custom.openaq.org/v3"
        assert cfg.openaq_api_key == "key123"
        assert cfg.lookback_hours == 6
        assert cfg.max_workers == 4
        assert cfg.http_timeout_seconds == 15
        assert cfg.dev_station_ids == ["101", "102"]
        assert cfg.max_sensors == 50
        assert cfg.enforce_complete_hours is False
        assert cfg.required_pollutants == ["o3", "pm10"]
        assert cfg.openaq_rate_limit_per_minute == 30
        assert cfg.openaq_rate_limit_per_hour == 1000
        assert cfg.max_http_attempts == 3
        assert cfg.progress_log_every == 10
        assert cfg.progress_log_interval_seconds == 15

    def test_minimal_config_uses_defaults(self) -> None:
        with patch.dict(os.environ, {"PROJECT_ID": "my-project"}, clear=True):
            cfg = Config.from_env()
        assert cfg.project_id == "my-project"
        assert cfg.raw_dataset == "air_quality_raw"
        assert cfg.lookback_hours == 3
        assert cfg.max_workers == 8
        assert cfg.enforce_complete_hours is True
        assert cfg.required_pollutants == ["no2", "pm10", "pm25"]
        assert cfg.openaq_api_key is None
        assert cfg.max_sensors is None
        assert cfg.dev_station_ids == []

    def test_missing_project_id_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="Could not determine GCP project ID"):
                Config.from_env()


# ---------------------------------------------------------------------------
# get_query_window
# ---------------------------------------------------------------------------


class TestGetQueryWindow:
    def test_typical_window(self) -> None:
        now = datetime(2024, 1, 15, 10, 5, 0, tzinfo=UTC)
        start, end = get_query_window(now, lookback_hours=3)
        assert start == datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        assert end == datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

    def test_window_at_midnight_boundary(self) -> None:
        now = datetime(2024, 1, 15, 0, 15, 0, tzinfo=UTC)
        start, end = get_query_window(now, lookback_hours=2)
        assert start == datetime(2024, 1, 14, 22, 0, 0, tzinfo=UTC)
        assert end == datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _transform_hour_row
# ---------------------------------------------------------------------------


class TestTransformHourRow:
    def _sensor(self, **overrides: object) -> Sensor:
        base: Sensor = {
            "station_id": 1,
            "openaq_location_id": 100,
            "openaq_sensor_id": 500,
            "pollutant": "pm10",
            "unit": "µg/m³",
        }
        base.update(overrides)
        return base

    def _hour_payload(self, **overrides: object) -> HourPayload:
        base: HourPayload = {
            "period": {
                "datetimeFrom": {"utc": "2024-01-15T09:00:00Z", "local": "2024-01-15T10:00:00+01:00"},
                "datetimeTo": {"utc": "2024-01-15T10:00:00Z", "local": "2024-01-15T11:00:00+01:00"},
                "label": "H09",
                "interval": 1,
            },
            "coverage": {
                "expectedCount": 60,
                "observedCount": 58,
                "percentComplete": 96.67,
            },
            "value": 25.5,
        }
        base.update(overrides)
        return base

    def test_valid_row(self) -> None:
        sensor = self._sensor()
        payload = self._hour_payload()
        ingested_at = "2024-01-15T10:05:00Z"
        run_id = "run123"
        window_end = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)

        row = _transform_hour_row(sensor, payload, ingested_at, run_id, window_end, enforce_complete_hours=True)
        assert row is not None
        assert row["station_id"] == 1
        assert row["openaq_location_id"] == 100
        assert row["openaq_sensor_id"] == 500
        assert row["pollutant"] == "pm10"
        assert row["value"] == 25.5
        assert row["unit"] == "µg/m³"
        assert row["period_from_utc"] == "2024-01-15T09:00:00Z"
        assert row["period_to_utc"] == "2024-01-15T10:00:00Z"
        assert row["period_from_local"] == "2024-01-15T10:00:00+01:00"
        assert row["period_label"] == "H09"
        assert row["period_interval"] == 1
        assert row["coverage_expected"] == 60
        assert row["coverage_observed"] == 58
        assert row["coverage_pct"] == 96.67
        assert row["dedup_key"] == "500|2024-01-15T09:00:00Z"
        assert row["ingested_at"] == ingested_at
        assert row["run_id"] == run_id

    def test_missing_period_from_utc_returns_none(self) -> None:
        sensor = self._sensor()
        payload = self._hour_payload()
        payload["period"]["datetimeFrom"] = {}
        row = _transform_hour_row(sensor, payload, "ts", "rid", datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC), True)
        assert row is None

    def test_missing_period_to_utc_returns_none(self) -> None:
        sensor = self._sensor()
        payload = self._hour_payload()
        payload["period"]["datetimeTo"] = {}
        row = _transform_hour_row(sensor, payload, "ts", "rid", datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC), True)
        assert row is None

    def test_null_value_returns_none(self) -> None:
        sensor = self._sensor()
        payload = self._hour_payload(value=None)
        row = _transform_hour_row(sensor, payload, "ts", "rid", datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC), True)
        assert row is None

    def test_enforce_complete_hours_filters_future(self) -> None:
        sensor = self._sensor()
        payload = self._hour_payload()
        payload["period"]["datetimeTo"]["utc"] = "2024-01-15T11:00:00Z"
        window_end = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        row = _transform_hour_row(sensor, payload, "ts", "rid", window_end, enforce_complete_hours=True)
        assert row is None

    def test_enforce_complete_hours_disabled_allows_future(self) -> None:
        sensor = self._sensor()
        payload = self._hour_payload()
        payload["period"]["datetimeTo"]["utc"] = "2024-01-15T11:00:00Z"
        window_end = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        row = _transform_hour_row(sensor, payload, "ts", "rid", window_end, enforce_complete_hours=False)
        assert row is not None
        assert row["period_to_utc"] == "2024-01-15T11:00:00Z"


# ---------------------------------------------------------------------------
# _determine_status
# ---------------------------------------------------------------------------


class TestDetermineStatus:
    def test_unhandled_exception_is_error(self) -> None:
        status, msg = _determine_status(PollingResult(), None, ValueError("boom"))
        assert status == "error"
        assert msg == "ValueError: boom"

    def test_bq_write_error_is_error(self) -> None:
        status, msg = _determine_status(PollingResult(), "BQ timeout", None)
        assert status == "error"
        assert msg == "BQ timeout"

    def test_no_rows_is_empty(self) -> None:
        status, msg = _determine_status(PollingResult(), None, None)
        assert status == "empty"
        assert msg is None

    def test_rows_with_failures_is_partial_success(self) -> None:
        result = PollingResult(rows=[_make_dummy_row()], failed_sensors=[{"sensor_id": 1, "station_id": 1, "pollutant": "pm10", "error_type": "Timeout", "error_message": "timeout"}])
        status, msg = _determine_status(result, None, None)
        assert status == "partial_success"
        assert msg is None

    def test_rows_no_failures_is_success(self) -> None:
        result = PollingResult(rows=[_make_dummy_row()])
        status, msg = _determine_status(result, None, None)
        assert status == "success"
        assert msg is None


# ---------------------------------------------------------------------------
# _build_summary
# ---------------------------------------------------------------------------


class TestBuildSummary:
    def test_basic_summary(self) -> None:
        cfg = _dummy_config()
        run_id = "run123"
        ingested_at = "2024-01-15T10:05:00Z"
        window_start = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        window_end = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        polling = PollingResult(
            rows=[_make_dummy_row(station_id=1), _make_dummy_row(station_id=2)],
            sensors_with_data={500, 501},
            stations_with_data={1, 2},
            api_calls=10,
            api_errors=1,
            sensors_queried=20,
        )

        summary = _build_summary(run_id, ingested_at, cfg, window_start, window_end, polling, 2, "partial_success", "some error")
        assert summary["source"] == "openaq"
        assert summary["run_id"] == "run123"
        assert summary["ingestion_timestamp"] == "2024-01-15T10:05:00Z"
        assert summary["record_count"] == 2
        assert summary["station_count"] == 2
        assert summary["sensors_queried"] == 20
        assert summary["sensors_with_data"] == 2
        assert summary["status"] == "partial_success"
        assert summary["error_message"] == "some error"
        assert summary["failed_sensor_count"] == 0
        assert summary["api_calls"] == 10
        assert summary["api_errors"] == 1

    def test_summary_no_data(self) -> None:
        cfg = _dummy_config()
        polling = PollingResult(sensors_queried=20)
        summary = _build_summary("r", "ts", cfg, datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), datetime(2024, 1, 1, 1, 0, 0, tzinfo=UTC), polling, 0, "empty", None)
        assert summary["data_timestamp_min"] is None
        assert summary["data_timestamp_max"] is None
        assert summary["record_count"] == 0
        assert summary["station_count"] == 0
        assert summary["sensors_with_data"] == 0


# ---------------------------------------------------------------------------
# _collect_future_result
# ---------------------------------------------------------------------------


class TestCollectFutureResult:
    def test_successful_future_with_rows(self) -> None:
        future = MagicMock()
        future.result.return_value = [_make_dummy_row(openaq_sensor_id=500)]
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        result = PollingResult()
        progress = MagicMock()

        _collect_future_result(future, sensor, result, progress)

        progress.record_success.assert_called_once_with(rows_count=1, had_data=True)
        assert result.api_calls == 1
        assert 500 in result.sensors_with_data
        assert 1 in result.stations_with_data
        assert len(result.rows) == 1

    def test_successful_future_no_rows(self) -> None:
        future = MagicMock()
        future.result.return_value = []
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        result = PollingResult()
        progress = MagicMock()

        _collect_future_result(future, sensor, result, progress)

        progress.record_success.assert_called_once_with(rows_count=0, had_data=False)
        assert result.api_calls == 1
        assert 500 not in result.sensors_with_data

    def test_failed_future_records_error(self) -> None:
        future = MagicMock()
        future.result.side_effect = RuntimeError("API timeout")
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        result = PollingResult()
        progress = MagicMock()

        _collect_future_result(future, sensor, result, progress)

        progress.record_failure.assert_called_once()
        assert result.api_errors == 1
        assert len(result.failed_sensors) == 1
        assert result.failed_sensors[0]["error_type"] == "RuntimeError"
        assert result.failed_sensors[0]["error_message"] == "API timeout"


# ---------------------------------------------------------------------------
# load_station_sensors
# ---------------------------------------------------------------------------


class TestLoadStationSensors:
    def _make_config(self, **overrides: object) -> Config:
        base = _dummy_config()
        for k, v in overrides.items():
            object.__setattr__(base, k, v)
        return base

    def test_loads_sensors(self) -> None:
        cfg = self._make_config()
        bq = MagicMock(spec=bigquery.Client)
        mock_rows = [
            {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"},
            {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 501, "pollutant": "no2", "unit": "µg/m³"},
        ]
        bq.query.return_value.result.return_value = mock_rows

        sensors = load_station_sensors(cfg, bq)
        assert len(sensors) == 2
        assert sensors[0]["openaq_sensor_id"] == 500
        bq.query.assert_called_once()

    def test_dev_station_ids_filter(self) -> None:
        cfg = self._make_config(dev_station_ids=["1"])
        bq = MagicMock(spec=bigquery.Client)
        bq.query.return_value.result.return_value = [{"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}]

        sensors = load_station_sensors(cfg, bq)
        assert len(sensors) == 1
        sql = bq.query.call_args[0][0]
        assert "station_ids" in sql

    def test_max_sensors_truncates(self) -> None:
        cfg = self._make_config(max_sensors=1)
        bq = MagicMock(spec=bigquery.Client)
        bq.query.return_value.result.return_value = [
            {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"},
            {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 501, "pollutant": "no2", "unit": "µg/m³"},
        ]

        sensors = load_station_sensors(cfg, bq)
        assert len(sensors) == 1

    def test_no_sensors_returns_empty_list(self) -> None:
        cfg = self._make_config()
        bq = MagicMock(spec=bigquery.Client)
        bq.query.return_value.result.return_value = []

        sensors = load_station_sensors(cfg, bq)
        assert sensors == []


# ---------------------------------------------------------------------------
# _append_rows_to_bigquery
# ---------------------------------------------------------------------------


class TestAppendRowsToBigquery:
    def test_writes_rows(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()
        rows = [_make_dummy_row()]

        count = _append_rows_to_bigquery(cfg, bq, rows)
        assert count == 1
        bq.load_table_from_json.assert_called_once()
        args, kwargs = bq.load_table_from_json.call_args
        assert args[1] == "test-project.raw.openaq_hourly"
        assert kwargs.get("job_config").write_disposition == bigquery.WriteDisposition.WRITE_APPEND
        assert kwargs.get("location") == "EU"

    def test_empty_rows_returns_zero(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()

        count = _append_rows_to_bigquery(cfg, bq, [])
        assert count == 0
        bq.load_table_from_json.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_sensor_hours
# ---------------------------------------------------------------------------


class TestFetchSensorHours:
    def _make_session(self) -> MagicMock:
        session = MagicMock(spec=requests.Session)
        session.get.return_value.status_code = 200
        session.get.return_value.json.return_value = {
            "results": [
                {
                    "period": {"datetimeFrom": {"utc": "2024-01-15T09:00:00Z"}, "datetimeTo": {"utc": "2024-01-15T10:00:00Z"}, "label": "H09", "interval": 1},
                    "coverage": {"expectedCount": 60, "observedCount": 58, "percentComplete": 96.67},
                    "value": 25.5,
                }
            ]
        }
        return session

    def test_successful_fetch(self) -> None:
        cfg = _dummy_config()
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        window_start = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        window_end = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        ingested_at = "2024-01-15T10:05:00Z"
        run_id = "run123"
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()

        rows = _fetch_sensor_hours(cfg, sensor, window_start, window_end, ingested_at, run_id, rate_limiter, progress, session)
        assert len(rows) == 1
        assert rows[0]["openaq_sensor_id"] == 500
        rate_limiter.acquire.assert_called()
        progress.record_http_attempt.assert_called_once()

    def test_retryable_status_code_retries_then_succeeds(self) -> None:
        cfg = _dummy_config()
        object.__setattr__(cfg, "max_http_attempts", 3)
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        ws = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        we = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        retry_resp = MagicMock(spec=requests.Response)
        retry_resp.status_code = 429
        retry_resp.headers = {}
        ok_resp = session.get.return_value
        session.get.side_effect = [retry_resp, retry_resp, ok_resp]

        with patch("ingestion.openaq_poller.main.time.sleep"):
            rows = _fetch_sensor_hours(cfg, sensor, ws, we, "ts", "rid", rate_limiter, progress, session)
        assert len(rows) == 1
        assert session.get.call_count == 3
        assert progress.record_retry.call_count == 2

    def test_retryable_status_exhausted_raises(self) -> None:
        cfg = _dummy_config()
        object.__setattr__(cfg, "max_http_attempts", 2)
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        ws = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        we = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        session.get.return_value.status_code = 503
        session.get.return_value.raise_for_status.side_effect = requests.HTTPError("503 Server Error")

        with pytest.raises(requests.HTTPError):
            with patch("ingestion.openaq_poller.main.time.sleep"):
                _fetch_sensor_hours(cfg, sensor, ws, we, "ts", "rid", rate_limiter, progress, session)
        assert session.get.call_count == 2

    def test_http_error_raised(self) -> None:
        cfg = _dummy_config()
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        ws = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        we = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        session.get.return_value.status_code = 403
        session.get.return_value.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")

        with pytest.raises(requests.HTTPError):
            _fetch_sensor_hours(cfg, sensor, ws, we, "ts", "rid", rate_limiter, progress, session)

    def test_network_error_retries(self) -> None:
        cfg = _dummy_config()
        object.__setattr__(cfg, "max_http_attempts", 2)
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        ws = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        we = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        session.get.side_effect = [requests.ConnectionError("connection lost"), session.get.return_value]

        with patch("ingestion.openaq_poller.main.time.sleep"):
            rows = _fetch_sensor_hours(cfg, sensor, ws, we, "ts", "rid", rate_limiter, progress, session)
        assert len(rows) == 1
        assert session.get.call_count == 2
        progress.record_retry.assert_called_once()

    def test_network_error_exhausted_raises(self) -> None:
        cfg = _dummy_config()
        object.__setattr__(cfg, "max_http_attempts", 2)
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        ws = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        we = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        session.get.side_effect = [requests.ConnectionError("fail"), requests.ConnectionError("fail again")]

        with pytest.raises(requests.ConnectionError):
            with patch("ingestion.openaq_poller.main.time.sleep"):
                _fetch_sensor_hours(cfg, sensor, ws, we, "ts", "rid", rate_limiter, progress, session)

    def test_value_error_json_retries_then_succeeds(self) -> None:
        cfg = _dummy_config()
        object.__setattr__(cfg, "max_http_attempts", 2)
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        ws = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        we = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()

        valid_json = {
            "results": [
                {
                    "period": {"datetimeFrom": {"utc": "2024-01-15T09:00:00Z"}, "datetimeTo": {"utc": "2024-01-15T10:00:00Z"}, "label": "H09", "interval": 1},
                    "coverage": {"expectedCount": 60, "observedCount": 58, "percentComplete": 96.67},
                    "value": 25.5,
                }
            ]
        }
        session.get.return_value.json.side_effect = [ValueError("bad json"), valid_json]

        with patch("ingestion.openaq_poller.main.time.sleep"):
            rows = _fetch_sensor_hours(cfg, sensor, ws, we, "ts", "rid", rate_limiter, progress, session)
        assert len(rows) == 1
        assert session.get.call_count == 2
        progress.record_retry.assert_called_once()

    def test_value_error_json_exhausted_raises(self) -> None:
        cfg = _dummy_config()
        object.__setattr__(cfg, "max_http_attempts", 2)
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        ws = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        we = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        session.get.return_value.json.side_effect = ValueError("bad json")

        with pytest.raises(ValueError):
            with patch("ingestion.openaq_poller.main.time.sleep"):
                _fetch_sensor_hours(cfg, sensor, ws, we, "ts", "rid", rate_limiter, progress, session)

    def test_fetch_filters_malformed_hours(self) -> None:
        cfg = _dummy_config()
        sensor: Sensor = {"station_id": 1, "openaq_location_id": 100, "openaq_sensor_id": 500, "pollutant": "pm10", "unit": "µg/m³"}
        ws = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        we = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()

        session.get.return_value.json.return_value = {
            "results": [
                {
                    "period": {"datetimeFrom": {"utc": "2024-01-15T09:00:00Z"}, "datetimeTo": {"utc": "2024-01-15T10:00:00Z"}, "label": "H09", "interval": 1},
                    "coverage": {"expectedCount": 60, "observedCount": 58, "percentComplete": 96.67},
                    "value": 25.5,
                },
                {
                    "period": {"datetimeFrom": {"utc": "2024-01-15T10:00:00Z"}, "datetimeTo": {"utc": "2024-01-15T11:00:00Z"}, "label": "H10", "interval": 1},
                    "coverage": {"expectedCount": 60, "observedCount": 0, "percentComplete": 0.0},
                    "value": None,
                },
            ]
        }

        rows = _fetch_sensor_hours(cfg, sensor, ws, we, "ts", "rid", rate_limiter, progress, session)
        assert len(rows) == 1
        assert rows[0]["period_label"] == "H09"


# ---------------------------------------------------------------------------
# _persist_rows
# ---------------------------------------------------------------------------


class TestPersistRows:
    def test_persists_rows_successfully(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()
        rows = [_make_dummy_row()]

        count, error = _persist_rows(cfg, bq, rows)
        assert count == 1
        assert error is None

    def test_no_rows_returns_zero(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()

        count, error = _persist_rows(cfg, bq, [])
        assert count == 0
        assert error is None

    def test_bq_failure_returns_error_message(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()
        rows = [_make_dummy_row()]

        with patch("ingestion.openaq_poller.main._append_rows_to_bigquery", side_effect=Exception("BQ timeout")):
            count, error = _persist_rows(cfg, bq, rows)
        assert count == 0
        assert error is not None
        assert "BQ timeout" in error


# ---------------------------------------------------------------------------
# _log_run
# ---------------------------------------------------------------------------


class TestLogRun:
    def test_delegates_to_write_ingestion_log(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()
        run_id = "run123"
        run_started_at = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        run_finished_at = datetime(2024, 1, 15, 10, 5, 0, tzinfo=UTC)
        window_start = datetime(2024, 1, 15, 7, 0, 0, tzinfo=UTC)
        window_end = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        polling = PollingResult(
            sensors_queried=20,
            api_calls=10,
            api_errors=1,
            failed_sensors=[{"sensor_id": 500, "station_id": 1, "pollutant": "pm10", "error_type": "Timeout", "error_message": "timeout"}],
        )

        with patch("ingestion.openaq_poller.main.write_ingestion_log") as mock_write:
            _log_run(bq, cfg, run_id, run_started_at, run_finished_at, window_start, window_end, 100, polling, "partial_success", "some error")

        mock_write.assert_called_once()
        kwargs = mock_write.call_args[1]
        assert kwargs["run_id"] == "run123"
        assert kwargs["source"] == "openaq"
        assert kwargs["status"] == "partial_success"
        assert kwargs["records_written"] == 100
        assert kwargs["sensors_queried"] == 20
        assert kwargs["sensors_failed"] == 1
        assert kwargs["api_calls"] == 10
        assert kwargs["api_errors"] == 1
        assert kwargs["error_message"] == "some error"
        assert kwargs["failed_sensor_ids"] == ["500"]


# ---------------------------------------------------------------------------
# run_poller
# ---------------------------------------------------------------------------


class TestRunPoller:
    def test_successful_run(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()
        dummy_rows = [_make_dummy_row()]

        with (
            patch("ingestion.openaq_poller.main._poll_sensors") as mock_poll,
            patch("ingestion.openaq_poller.main._persist_rows", return_value=(1, None)),
        ):
            mock_poll.return_value = PollingResult(
                rows=dummy_rows,
                sensors_with_data={500},
                stations_with_data={1},
                api_calls=5,
                api_errors=0,
                sensors_queried=1,
            )
            summary = run_poller(cfg, bq)
        assert summary["status"] == "success"
        assert summary["source"] == "openaq"
        assert summary["run_id"] is not None
        assert summary["record_count"] == 1
        assert summary["sensors_queried"] == 1
        assert summary["api_calls"] == 5

    def test_no_sensors_returns_empty(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        bq.query.return_value.result.return_value = []
        cfg = _dummy_config()

        summary = run_poller(cfg, bq)
        assert summary["status"] == "empty"
        assert summary["record_count"] == 0

    def test_unhandled_exception_returns_error(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()

        with patch("ingestion.openaq_poller.main._poll_sensors", side_effect=ValueError("db connection lost")):
            summary = run_poller(cfg, bq)
        assert summary["status"] == "error"
        assert "db connection lost" in summary["error_message"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dummy_config() -> Config:
    return Config(
        project_id="test-project",
        raw_dataset="raw",
        station_sensors_table="station_sensors",
        hourly_table="openaq_hourly",
        bq_location="EU",
        openaq_base_url="https://api.openaq.org/v3",
        openaq_api_key=None,
        lookback_hours=3,
        max_workers=2,
        http_timeout_seconds=30,
        dev_station_ids=[],
        max_sensors=None,
        enforce_complete_hours=True,
        required_pollutants=["no2", "pm10", "pm25"],
        openaq_rate_limit_per_minute=60,
        openaq_rate_limit_per_hour=2000,
        max_http_attempts=2,
        progress_log_every=25,
        progress_log_interval_seconds=30,
    )


def _make_dummy_row(**overrides: object) -> HourRow:
    base: HourRow = {
        "ingested_at": "2024-01-15T10:05:00Z",
        "run_id": "run123",
        "station_id": 1,
        "openaq_location_id": 100,
        "openaq_sensor_id": 500,
        "pollutant": "pm10",
        "value": 25.5,
        "unit": "µg/m³",
        "period_from_utc": "2024-01-15T09:00:00Z",
        "period_to_utc": "2024-01-15T10:00:00Z",
        "period_from_local": "2024-01-15T10:00:00+01:00",
        "period_label": "H09",
        "period_interval": 1,
        "coverage_expected": 60,
        "coverage_observed": 58,
        "coverage_pct": 96.67,
        "dedup_key": "500|2024-01-15T09:00:00Z",
    }
    base.update(overrides)
    return base
