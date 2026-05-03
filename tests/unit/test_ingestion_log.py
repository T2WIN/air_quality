"""Unit tests for ingestion.shared.ingestion_log."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from google.cloud import bigquery

from ingestion.shared.ingestion_log import write_ingestion_log


class TestWriteIngestionLog:
    """write_ingestion_log() — BQ ingestion log writer."""

    def _make_client(self) -> MagicMock:
        bq_client = MagicMock(spec=bigquery.Client)
        bq_client.load_table_from_json.return_value.result.return_value = None
        return bq_client

    def _default_kwargs(self, **overrides: object) -> dict:
        base: dict = {
            "project_id": "my-project",
            "raw_dataset": "raw_dataset",
            "run_id": "20240115T103000Z_a1b2c3d4",
            "source": "openaq",
            "status": "success",
            "run_started_at": datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
            "run_finished_at": datetime(2024, 1, 15, 10, 5, 0, tzinfo=UTC),
            "records_written": 100,
            "sensors_queried": 50,
            "sensors_failed": 2,
            "stations_polled": 10,
            "stations_failed": 1,
            "api_calls": 60,
            "api_errors": 3,
            "window_start": datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC),
            "window_end": datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
            "error_message": None,
            "failed_sensor_ids": None,
            "failed_station_ids": None,
        }
        base.update(overrides)
        return base

    def _run(self, **overrides: object) -> MagicMock:
        bq_client = self._make_client()
        kwargs = self._default_kwargs(**overrides)
        now = datetime(2024, 1, 15, 10, 5, 0, tzinfo=UTC)
        with patch("ingestion.shared.ingestion_log.utc_now", return_value=now):
            write_ingestion_log(bq_client, **kwargs)
        return bq_client

    def _capture_row(self, bq_client: MagicMock) -> dict:
        return bq_client.load_table_from_json.call_args[0][0][0]

    def test_all_fields_populated(self) -> None:
        bq_client = self._run()
        row = self._capture_row(bq_client)

        assert row["run_id"] == "20240115T103000Z_a1b2c3d4"
        assert row["source"] == "openaq"
        assert row["status"] == "success"
        assert row["run_started_at"] == "2024-01-15T10:00:00Z"
        assert row["run_finished_at"] == "2024-01-15T10:05:00Z"
        assert row["records_written"] == 100
        assert row["sensors_queried"] == 50
        assert row["sensors_failed"] == 2
        assert row["stations_polled"] == 10
        assert row["stations_failed"] == 1
        assert row["api_calls"] == 60
        assert row["api_errors"] == 3
        assert row["window_start_utc"] == "2024-01-15T09:00:00Z"
        assert row["window_end_utc"] == "2024-01-15T10:00:00Z"
        assert row["ingested_at"] == "2024-01-15T10:05:00Z"

    def test_optionals_as_none(self) -> None:
        bq_client = self._run(
            sensors_queried=None,
            sensors_failed=None,
            stations_polled=None,
            stations_failed=None,
            window_start=None,
            window_end=None,
            error_message=None,
            failed_sensor_ids=None,
            failed_station_ids=None,
        )
        row = self._capture_row(bq_client)
        assert row["sensors_queried"] is None
        assert row["sensors_failed"] is None
        assert row["stations_polled"] is None
        assert row["stations_failed"] is None
        assert row["window_start_utc"] is None
        assert row["window_end_utc"] is None
        assert row["error_message"] is None
        assert row["failed_sensor_ids"] is None
        assert row["failed_station_ids"] is None

    def test_duration_seconds_computed(self) -> None:
        bq_client = self._run(
            run_started_at=datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
            run_finished_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
        )
        row = self._capture_row(bq_client)
        assert row["duration_seconds"] == 1800.0

    def test_failed_sensor_ids_json(self) -> None:
        failed = [{"sensor_id": 123, "reason": "timeout"}]
        bq_client = self._run(failed_sensor_ids=failed)
        row = self._capture_row(bq_client)
        assert row["failed_sensor_ids"] == json.dumps(failed)

    def test_failed_station_ids_json(self) -> None:
        failed = ["FR001", "FR002"]
        bq_client = self._run(failed_station_ids=failed)
        row = self._capture_row(bq_client)
        assert row["failed_station_ids"] == json.dumps(failed)

    def test_error_message_included(self) -> None:
        bq_client = self._run(error_message="Something went wrong")
        row = self._capture_row(bq_client)
        assert row["error_message"] == "Something went wrong"

    def test_table_id_format(self) -> None:
        bq_client = self._run()
        table_id = bq_client.load_table_from_json.call_args[0][1]
        assert table_id == "my-project.raw_dataset.ingestion_log"

    def test_bq_failure_caught_and_logged(self) -> None:
        bq_client = self._make_client()
        bq_client.load_table_from_json.side_effect = Exception("BQ timeout")
        kwargs = self._default_kwargs()
        now = datetime(2024, 1, 15, 10, 5, 0, tzinfo=UTC)
        with patch("ingestion.shared.ingestion_log.utc_now", return_value=now):
            write_ingestion_log(bq_client, **kwargs)
        bq_client.load_table_from_json.assert_called_once()
