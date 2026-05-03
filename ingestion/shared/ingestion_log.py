"""Shared ingestion log writer for all pollers."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from google.cloud import bigquery

from .datetime_utils import to_rfc3339_z, utc_now

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)

INGESTION_LOG_TABLE = "ingestion_log"


def write_ingestion_log(
    bq_client: bigquery.Client,
    project_id: str,
    raw_dataset: str,
    run_id: str,
    source: str,
    status: str,
    run_started_at: datetime,
    run_finished_at: datetime,
    records_written: int,
    sensors_queried: int | None,
    sensors_failed: int | None,
    stations_polled: int | None,
    stations_failed: int | None,
    api_calls: int,
    api_errors: int,
    window_start: datetime | None,
    window_end: datetime | None,
    error_message: str | None,
    failed_sensor_ids: list[dict[str, Any]] | None,
    failed_station_ids: list[str] | None,
) -> None:
    """Write a row to the ingestion_log table.

    Args:
        bq_client: BigQuery client instance (injected)
        project_id: GCP project ID
        raw_dataset: BigQuery raw dataset name
        run_id: Unique run identifier
        source: Source identifier (e.g., "openaq", "open-meteo")
        status: Run status (success, partial_success, error, empty)
        run_started_at: When the run started
        run_finished_at: When the run finished
        records_written: Number of records written to BQ
        sensors_queried: Number of sensors queried (if applicable)
        sensors_failed: Number of sensors that failed (if applicable)
        stations_polled: Number of stations polled
        stations_failed: Number of stations that failed
        api_calls: Total API calls made
        api_errors: Number of API errors
        window_start: Start of data window (if applicable)
        window_end: End of data window (if applicable)
        error_message: Error message if status is error
        failed_sensor_ids: List of failed sensor details
        failed_station_ids: List of failed station IDs
    """
    table_id = f"{project_id}.{raw_dataset}.{INGESTION_LOG_TABLE}"

    row = {
        "run_id": run_id,
        "source": source,
        "status": status,
        "run_started_at": to_rfc3339_z(run_started_at),
        "run_finished_at": to_rfc3339_z(run_finished_at),
        "duration_seconds": (run_finished_at - run_started_at).total_seconds(),
        "records_written": records_written,
        "sensors_queried": sensors_queried,
        "sensors_failed": sensors_failed,
        "stations_polled": stations_polled,
        "stations_failed": stations_failed,
        "api_calls": api_calls,
        "api_errors": api_errors,
        "window_start_utc": to_rfc3339_z(window_start) if window_start else None,
        "window_end_utc": to_rfc3339_z(window_end) if window_end else None,
        "error_message": error_message,
        "failed_sensor_ids": json.dumps(failed_sensor_ids) if failed_sensor_ids else None,
        "failed_station_ids": json.dumps(failed_station_ids) if failed_station_ids else None,
        "ingested_at": to_rfc3339_z(utc_now()),
    }

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    try:
        bq_client.load_table_from_json([row], table_id, job_config=job_config).result()
        logger.info(
            "Wrote ingestion_log row: run_id=%s status=%s records=%d",
            run_id,
            status,
            records_written,
        )
    except Exception as exc:
        logger.exception("Failed to write ingestion_log row: %s", exc)
