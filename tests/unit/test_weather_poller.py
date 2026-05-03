"""Unit tests for ingestion.weather_poller.main."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from google.cloud import bigquery

from ingestion.weather_poller.main import (
    Config,
    OpenMeteoResult,
    PollingResult,
    StationLocation,
    WeatherRow,
    _append_rows_to_bigquery,
    _build_summary,
    _determine_status,
    _fetch_batch_with_retry,
    _log_run,
    _persist_rows,
    _poll_batch,
    _poll_stations,
    load_station_locations,
    parse_batch,
    run_poller,
)

# ---------------------------------------------------------------------------
# Config.from_env
# ---------------------------------------------------------------------------


class TestConfigFromEnv:
    def test_basic_config(self) -> None:
        env = {
            "PROJECT_ID": "my-project",
            "BQ_RAW_DATASET": "raw",
            "BQ_WEATHER_TABLE": "forecasts",
            "BQ_STATION_METADATA_TABLE": "stations",
            "BQ_LOCATION": "US",
            "BATCH_SIZE": "100",
            "FORECAST_HOURS": "72",
            "OPEN_METEO_RATE_LIMIT_PER_MINUTE": "200",
            "OPEN_METEO_RATE_LIMIT_PER_HOUR": "500",
            "OPEN_METEO_URL": "https://custom.meteo.com/forecast",
            "HTTP_TIMEOUT_SECONDS": "15",
            "MAX_HTTP_ATTEMPTS": "3",
            "MAX_BATCHES": "10",
            "PROGRESS_LOG_EVERY": "10",
            "PROGRESS_LOG_INTERVAL_SECONDS": "15",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
        assert cfg.project_id == "my-project"
        assert cfg.raw_dataset == "raw"
        assert cfg.weather_table == "forecasts"
        assert cfg.station_metadata_table == "stations"
        assert cfg.bq_location == "US"
        assert cfg.batch_size == 100
        assert cfg.forecast_hours == 72
        assert cfg.open_meteo_rate_limit_per_minute == 200
        assert cfg.open_meteo_rate_limit_per_hour == 500
        assert cfg.open_meteo_url == "https://custom.meteo.com/forecast"
        assert cfg.http_timeout_seconds == 15
        assert cfg.max_http_attempts == 3
        assert cfg.max_batches == 10
        assert cfg.progress_log_every == 10
        assert cfg.progress_log_interval_seconds == 15

    def test_minimal_config_uses_defaults(self) -> None:
        with patch.dict(os.environ, {"PROJECT_ID": "my-project"}, clear=True):
            cfg = Config.from_env()
        assert cfg.project_id == "my-project"
        assert cfg.raw_dataset == "air_quality_raw"
        assert cfg.weather_table == "weather_forecasts"
        assert cfg.batch_size == 50
        assert cfg.forecast_hours == 48
        assert cfg.open_meteo_rate_limit_per_minute == 300
        assert cfg.open_meteo_rate_limit_per_hour == 1000
        assert cfg.max_batches is None

    def test_missing_project_id_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True), pytest.raises(ValueError, match="Could not determine GCP project ID"):
            Config.from_env()


# ---------------------------------------------------------------------------
# load_station_locations
# ---------------------------------------------------------------------------


class TestLoadStationLocations:
    def test_loads_locations(self) -> None:
        cfg = _dummy_config()
        bq = MagicMock(spec=bigquery.Client)
        mock_rows = [
            {"station_id": 1, "latitude": 48.8566, "longitude": 2.3522},
            {"station_id": 2, "latitude": 45.7640, "longitude": 4.8357},
        ]
        bq.query.return_value.result.return_value = mock_rows

        locations = load_station_locations(cfg, bq)
        assert len(locations) == 2
        assert locations[0]["station_id"] == 1
        bq.query.assert_called_once()

    def test_no_locations_returns_empty_list(self) -> None:
        cfg = _dummy_config()
        bq = MagicMock(spec=bigquery.Client)
        bq.query.return_value.result.return_value = []

        locations = load_station_locations(cfg, bq)
        assert locations == []


# ---------------------------------------------------------------------------
# parse_batch
# ---------------------------------------------------------------------------


class TestParseBatch:
    def _make_result(self, lat: float, lon: float, times: list[str] | None = None) -> OpenMeteoResult:
        return {
            "latitude": lat,
            "longitude": lon,
            "hourly": {
                "time": times or ["2024-01-15T09:00", "2024-01-15T10:00"],
                "temperature_2m": [12.5, 13.0],
                "relative_humidity_2m": [80, 75],
                "surface_pressure": [1013, 1012],
                "wind_speed_10m": [5.0, 6.0],
                "wind_direction_10m": [180, 190],
                "precipitation": [0.0, 0.5],
                "cloud_cover": [50, 60],
                "boundary_layer_height": [200, 250],
            },
        }

    def test_parses_single_station(self) -> None:
        api_results = [self._make_result(48.8566, 2.3522)]
        rows = parse_batch(api_results, [1], [48.8566], [2.3522], "2024-01-15T10:05:00Z", "run123")
        assert len(rows) == 2
        assert rows[0]["station_id"] == 1
        assert rows[0]["temperature_2m"] == 12.5
        assert rows[0]["valid_time"] == "2024-01-15T09:00:00+00:00"
        assert rows[1]["valid_time"] == "2024-01-15T10:00:00+00:00"
        assert rows[0]["dedup_key"] == "1|2024-01-15T09:00:00+00:00"

    def test_parses_multiple_stations(self) -> None:
        api_results = [
            self._make_result(48.8566, 2.3522, times=["2024-01-15T09:00"]),
            self._make_result(45.7640, 4.8357, times=["2024-01-15T09:00"]),
        ]
        rows = parse_batch(api_results, [1, 2], [48.8566, 45.7640], [2.3522, 4.8357], "ts", "rid")
        assert len(rows) == 2
        assert rows[0]["station_id"] == 1
        assert rows[1]["station_id"] == 2

    def test_returns_empty_on_mismatched_counts(self) -> None:
        rows = parse_batch([self._make_result(48.8566, 2.3522)], [1, 2], [48.8566, 45.7640], [2.3522, 4.8357], "ts", "rid")
        assert rows == []

    def test_handles_missing_hourly_fields(self) -> None:
        result: OpenMeteoResult = {
            "latitude": 48.8566,
            "longitude": 2.3522,
            "hourly": {
                "time": ["2024-01-15T09:00"],
            },
        }
        rows = parse_batch([result], [1], [48.8566], [2.3522], "ts", "rid")
        assert len(rows) == 1
        assert rows[0]["temperature_2m"] is None

    def test_handles_already_formatted_timestamp(self) -> None:
        result = self._make_result(48.8566, 2.3522, times=["2024-01-15T09:00:00+00:00"])
        rows = parse_batch([result], [1], [48.8566], [2.3522], "ts", "rid")
        assert rows[0]["valid_time"] == "2024-01-15T09:00:00+00:00"


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

    def test_bq_write_and_unhandled_exception_both_error(self) -> None:
        status, msg = _determine_status(PollingResult(), "BQ timeout", RuntimeError("crash"))
        assert status == "error"
        assert msg == "RuntimeError: crash"

    def test_no_rows_is_empty(self) -> None:
        status, msg = _determine_status(PollingResult(), None, None)
        assert status == "empty"
        assert msg is None

    def test_rows_with_failures_is_partial_success(self) -> None:
        result = PollingResult(
            rows=[_make_dummy_weather_row()],
            failed_stations=[{"station_id": "1", "error_type": "Timeout", "error_message": "timeout"}],
        )
        status, msg = _determine_status(result, None, None)
        assert status == "partial_success"
        assert msg is None

    def test_rows_no_failures_is_success(self) -> None:
        result = PollingResult(rows=[_make_dummy_weather_row()])
        status, msg = _determine_status(result, None, None)
        assert status == "success"
        assert msg is None


# ---------------------------------------------------------------------------
# _build_summary
# ---------------------------------------------------------------------------


class TestBuildSummary:
    def test_basic_summary(self) -> None:
        cfg = _dummy_config()
        polling = PollingResult(
            rows=[_make_dummy_weather_row(), _make_dummy_weather_row(station_id=2)],
            api_calls=10,
            api_errors=1,
            stations_polled=2,
            stations_with_data=2,
            failed_stations=[{"station_id": "3", "error_type": "Timeout", "error_message": "timeout"}],
        )
        summary = _build_summary("run123", "ts", cfg, polling, 2, "partial_success", None)
        assert summary["source"] == "open-meteo"
        assert summary["run_id"] == "run123"
        assert summary["record_count"] == 2
        assert summary["stations_polled"] == 2
        assert summary["stations_with_data"] == 2
        assert summary["status"] == "partial_success"
        assert summary["error_message"] is None
        assert summary["failed_station_count"] == 1
        assert summary["api_calls"] == 10
        assert summary["api_errors"] == 1

    def test_empty_summary(self) -> None:
        cfg = _dummy_config()
        polling = PollingResult()
        summary = _build_summary("rid", "ts", cfg, polling, 0, "empty", None)
        assert summary["record_count"] == 0
        assert summary["stations_polled"] == 0
        assert summary["stations_with_data"] == 0
        assert summary["status"] == "empty"


# ---------------------------------------------------------------------------
# _append_rows_to_bigquery
# ---------------------------------------------------------------------------


class TestAppendRowsToBigquery:
    def test_writes_rows(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()
        rows = [_make_dummy_weather_row()]

        count = _append_rows_to_bigquery(cfg, bq, rows)
        assert count == 1
        bq.load_table_from_json.assert_called_once()
        args, kwargs = bq.load_table_from_json.call_args
        assert args[1] == "test-project.raw.weather_forecasts"
        assert kwargs.get("job_config").write_disposition == bigquery.WriteDisposition.WRITE_APPEND
        assert kwargs.get("location") == "EU"

    def test_empty_rows_returns_zero(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()

        count = _append_rows_to_bigquery(cfg, bq, [])
        assert count == 0
        bq.load_table_from_json.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_batch_with_retry
# ---------------------------------------------------------------------------


class TestFetchBatchWithRetry:
    def _make_session(self) -> MagicMock:
        session = MagicMock(spec=requests.Session)
        session.get.return_value.status_code = 200
        session.get.return_value.json.return_value = {
            "latitude": 48.8566,
            "longitude": 2.3522,
            "hourly": {
                "time": ["2024-01-15T09:00"],
                "temperature_2m": [12.5],
                "relative_humidity_2m": [80],
                "surface_pressure": [1013],
                "wind_speed_10m": [5.0],
                "wind_direction_10m": [180],
                "precipitation": [0.0],
                "cloud_cover": [50],
                "boundary_layer_height": [200],
            },
        }
        return session

    def _config(self, **overrides: Any) -> Config:
        cfg = _dummy_config()
        for k, v in overrides.items():
            object.__setattr__(cfg, k, v)
        return cfg

    def test_successful_fetch(self) -> None:
        cfg = self._config()
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()

        results = _fetch_batch_with_retry(cfg, [48.8566], [2.3522], rate_limiter, progress, session)
        assert len(results) == 1
        assert results[0]["latitude"] == 48.8566
        rate_limiter.acquire.assert_called_with(count=1)
        progress.record_http_attempt.assert_called_once()

    def test_multi_location_returns_list(self) -> None:
        cfg = self._config()
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        session.get.return_value.json.return_value = [
            {"latitude": 48.8566, "longitude": 2.3522, "hourly": {"time": [], "temperature_2m": []}},
            {"latitude": 45.7640, "longitude": 4.8357, "hourly": {"time": [], "temperature_2m": []}},
        ]

        results = _fetch_batch_with_retry(cfg, [48.8566, 45.7640], [2.3522, 4.8357], rate_limiter, progress, session)
        assert len(results) == 2
        rate_limiter.acquire.assert_called_with(count=2)

    def test_retryable_status_retries_then_succeeds(self) -> None:
        cfg = self._config(max_http_attempts=3)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        retry_resp = MagicMock(spec=requests.Response)
        retry_resp.status_code = 429
        retry_resp.headers = {}
        ok_resp = session.get.return_value
        session.get.side_effect = [retry_resp, retry_resp, ok_resp]

        with patch("ingestion.weather_poller.main.time.sleep"):
            results = _fetch_batch_with_retry(cfg, [48.8566], [2.3522], rate_limiter, progress, session)
        assert len(results) == 1
        assert session.get.call_count == 3
        assert progress.record_retry.call_count == 2

    def test_retryable_status_exhausted_raises(self) -> None:
        cfg = self._config(max_http_attempts=2)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        session.get.return_value.status_code = 503
        session.get.return_value.raise_for_status.side_effect = requests.HTTPError("503 Server Error")

        with pytest.raises(requests.HTTPError), patch("ingestion.weather_poller.main.time.sleep"):
            _fetch_batch_with_retry(cfg, [48.8566], [2.3522], rate_limiter, progress, session)
        assert session.get.call_count == 2

    def test_non_retryable_status_raises_immediately(self) -> None:
        cfg = self._config()
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        session.get.return_value.status_code = 403
        session.get.return_value.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")

        with pytest.raises(requests.HTTPError):
            _fetch_batch_with_retry(cfg, [48.8566], [2.3522], rate_limiter, progress, session)

    def test_network_error_retries_then_succeeds(self) -> None:
        cfg = self._config(max_http_attempts=2)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        session.get.side_effect = [requests.ConnectionError("connection lost"), session.get.return_value]

        with patch("ingestion.weather_poller.main.time.sleep"):
            results = _fetch_batch_with_retry(cfg, [48.8566], [2.3522], rate_limiter, progress, session)
        assert len(results) == 1
        assert session.get.call_count == 2
        progress.record_retry.assert_called_once()

    def test_network_error_exhausted_raises(self) -> None:
        cfg = self._config(max_http_attempts=2)
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = self._make_session()
        session.get.side_effect = [requests.ConnectionError("fail"), requests.ConnectionError("fail again")]

        with pytest.raises(requests.ConnectionError), patch("ingestion.weather_poller.main.time.sleep"):
            _fetch_batch_with_retry(cfg, [48.8566], [2.3522], rate_limiter, progress, session)


# ---------------------------------------------------------------------------
# _persist_rows
# ---------------------------------------------------------------------------


class TestPersistRows:
    def test_persists_rows_successfully(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()
        rows = [_make_dummy_weather_row()]

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
        rows = [_make_dummy_weather_row()]

        with patch("ingestion.weather_poller.main._append_rows_to_bigquery", side_effect=Exception("BQ timeout")):
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
        polling = PollingResult(
            stations_polled=2,
            api_calls=5,
            api_errors=0,
            failed_stations=[{"station_id": "1", "error_type": "Timeout", "error_message": "timeout"}],
        )

        with patch("ingestion.weather_poller.main.write_ingestion_log") as mock_write:
            _log_run(bq, cfg, run_id, run_started_at, run_finished_at, 100, polling, "partial_success", None)

        mock_write.assert_called_once()
        kwargs = mock_write.call_args[1]
        assert kwargs["run_id"] == "run123"
        assert kwargs["source"] == "open-meteo"
        assert kwargs["status"] == "partial_success"
        assert kwargs["records_written"] == 100
        assert kwargs["stations_polled"] == 2
        assert kwargs["stations_failed"] == 1
        assert kwargs["api_calls"] == 5
        assert kwargs["api_errors"] == 0
        assert kwargs["error_message"] is None
        assert kwargs["failed_station_ids"] == ["1"]


# ---------------------------------------------------------------------------
# _poll_batch
# ---------------------------------------------------------------------------


class TestPollBatch:
    def test_successful_batch(self) -> None:
        cfg = _dummy_config()
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = MagicMock(spec=requests.Session)
        result = PollingResult()

        api_results = [
            {"latitude": 48.8566, "longitude": 2.3522, "hourly": {"time": ["2024-01-15T09:00"], "temperature_2m": [12.5]}},
        ]

        with (
            patch("ingestion.weather_poller.main._fetch_batch_with_retry", return_value=api_results),
            patch("ingestion.weather_poller.main.parse_batch", return_value=[_make_dummy_weather_row()]),
        ):
            _poll_batch(cfg, [{"station_id": 1, "latitude": 48.8566, "longitude": 2.3522}], rate_limiter, progress, session, "ts", result, 1, "run123")

        assert result.stations_polled == 1
        assert result.stations_with_data == 1
        assert result.api_calls == 1
        assert len(result.rows) == 1

    def test_failed_batch_records_error(self) -> None:
        cfg = _dummy_config()
        rate_limiter = MagicMock()
        progress = MagicMock()
        session = MagicMock(spec=requests.Session)
        result = PollingResult()

        with (
            patch("ingestion.weather_poller.main._fetch_batch_with_retry", side_effect=RuntimeError("API error")),
        ):
            _poll_batch(cfg, [{"station_id": 1, "latitude": 48.8566, "longitude": 2.3522}], rate_limiter, progress, session, "ts", result, 1, "run123")

        assert result.stations_polled == 1
        assert result.api_errors == 1
        assert len(result.failed_stations) == 1
        assert result.failed_stations[0]["error_type"] == "RuntimeError"
        progress.record_failure.assert_called_once()


# ---------------------------------------------------------------------------
# _poll_stations
# ---------------------------------------------------------------------------


class TestPollStations:
    def test_polls_all_batches(self) -> None:
        cfg = _dummy_config()
        bq = MagicMock(spec=bigquery.Client)
        rate_limiter = MagicMock()

        stations: list[StationLocation] = [
            {"station_id": i, "latitude": 48.0 + i * 0.01, "longitude": 2.0 + i * 0.01}
            for i in range(5)
        ]

        with (
            patch("ingestion.weather_poller.main.load_station_locations", return_value=stations),
            patch("ingestion.weather_poller.main.get_session") as mock_get_session,
            patch("ingestion.weather_poller.main.ProgressTracker") as mock_tracker_cls,
            patch("ingestion.weather_poller.main._poll_batch") as mock_poll_batch,
        ):
            mock_session = MagicMock()
            mock_get_session.return_value = mock_session
            mock_tracker = MagicMock()
            mock_tracker_cls.return_value = mock_tracker

            result = _poll_stations(cfg, bq, rate_limiter, "ts", "run123")

        mock_tracker.start.assert_called_once()
        mock_tracker.stop.assert_called_once()
        assert result.stations_polled == 0
        assert mock_poll_batch.call_count == 1

    def test_no_stations_returns_empty(self) -> None:
        cfg = _dummy_config()
        bq = MagicMock(spec=bigquery.Client)
        rate_limiter = MagicMock()

        with (
            patch("ingestion.weather_poller.main.load_station_locations", return_value=[]),
        ):
            result = _poll_stations(cfg, bq, rate_limiter, "ts", "run123")

        assert result.stations_polled == 0
        assert result.rows == []
        assert result.api_calls == 0

    def test_max_batches_limits_polling(self) -> None:
        cfg = _dummy_config()
        object.__setattr__(cfg, "batch_size", 2)
        object.__setattr__(cfg, "max_batches", 2)
        bq = MagicMock(spec=bigquery.Client)
        rate_limiter = MagicMock()

        stations = [
            {"station_id": i, "latitude": 48.0 + i * 0.01, "longitude": 2.0 + i * 0.01}
            for i in range(10)
        ]

        with (
            patch("ingestion.weather_poller.main.load_station_locations", return_value=stations),
            patch("ingestion.weather_poller.main.get_session"),
            patch("ingestion.weather_poller.main.ProgressTracker"),
            patch("ingestion.weather_poller.main._poll_batch") as mock_poll_batch,
        ):
            _poll_stations(cfg, bq, rate_limiter, "ts", "run123")

        assert mock_poll_batch.call_count == 2


# ---------------------------------------------------------------------------
# run_poller
# ---------------------------------------------------------------------------


class TestRunPoller:
    def test_successful_run(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()
        dummy_rows = [_make_dummy_weather_row()]

        with (
            patch("ingestion.weather_poller.main._poll_stations") as mock_poll,
            patch("ingestion.weather_poller.main._persist_rows", return_value=(1, None)),
        ):
            mock_poll.return_value = PollingResult(
                rows=dummy_rows,
                stations_polled=5,
                stations_with_data=5,
                api_calls=5,
                api_errors=0,
            )
            summary = run_poller(cfg, bq)
        assert summary["status"] == "success"
        assert summary["source"] == "open-meteo"
        assert summary["run_id"] is not None
        assert summary["record_count"] == 1
        assert summary["stations_polled"] == 5
        assert summary["api_calls"] == 5

    def test_unhandled_exception_returns_error(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        cfg = _dummy_config()

        with patch("ingestion.weather_poller.main._poll_stations", side_effect=ValueError("db connection lost")):
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
        weather_table="weather_forecasts",
        station_metadata_table="station_metadata",
        bq_location="EU",
        batch_size=50,
        forecast_hours=48,
        open_meteo_rate_limit_per_minute=300,
        open_meteo_rate_limit_per_hour=1000,
        open_meteo_url="https://api.open-meteo.com/v1/forecast",
        http_timeout_seconds=30,
        max_http_attempts=2,
        max_batches=None,
        progress_log_every=25,
        progress_log_interval_seconds=30,
    )


def _make_dummy_weather_row(**overrides: object) -> WeatherRow:
    base: WeatherRow = {
        "run_id": "run123",
        "station_id": 1,
        "latitude": 48.8566,
        "longitude": 2.3522,
        "valid_time": "2024-01-15T09:00:00+00:00",
        "ingested_at": "2024-01-15T10:05:00Z",
        "dedup_key": "1|2024-01-15T09:00:00+00:00",
        "temperature_2m": 12.5,
        "relative_humidity_2m": 80,
        "surface_pressure": 1013,
        "wind_speed_10m": 5.0,
        "wind_direction_10m": 180,
        "precipitation": 0.0,
        "cloud_cover": 50,
        "boundary_layer_height": 200,
    }
    base.update(overrides)
    return base
