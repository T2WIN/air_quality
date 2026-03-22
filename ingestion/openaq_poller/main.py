"""OpenAQ hourly-data poller – Cloud Run job.

Fetches pre-aggregated hourly measurements from the OpenAQ v3 API for
every sensor registered in the ``station_sensors`` lookup table if the
sensor belongs to a station that aggregates all the required pollutants,
then appends the rows to a BigQuery raw table.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TypedDict

import requests
from google.cloud import bigquery

from ingestion.shared import (
    DualWindowRateLimiter,
    ProgressTracker,
    backoff_seconds,
    build_run_id,
    deep_get,
    get_session,
    parse_csv_env,
    parse_optional_int,
    parse_timestamp,
    to_rfc3339_z,
    utc_now,
    write_ingestion_log,
)


# ---------------------------------------------------------------------------
# TypedDicts for complex data structures
# ---------------------------------------------------------------------------


class Sensor(TypedDict):
    """Sensor record loaded from BigQuery station_sensors table."""

    station_id: int
    openaq_location_id: int
    openaq_sensor_id: int
    pollutant: str
    unit: str


class PeriodInfo(TypedDict):
    """Nested period information within an hour payload."""

    datetimeFrom: dict[str, str]
    datetimeTo: dict[str, str]
    label: str | None
    interval: int | None


class CoverageInfo(TypedDict):
    """Nested coverage information within an hour payload."""

    expectedCount: int | None
    observedCount: int | None
    percentComplete: float | None


class HourPayload(TypedDict):
    """Raw hour data payload from OpenAQ API response."""

    period: PeriodInfo
    coverage: CoverageInfo
    value: float | None


class HourRow(TypedDict):
    """Transformed hour row ready for BigQuery insert."""

    ingested_at: str
    run_id: str
    station_id: int
    openaq_location_id: int
    openaq_sensor_id: int
    pollutant: str
    value: float
    unit: str | None
    period_from_utc: str
    period_to_utc: str
    period_from_local: str | None
    period_label: str | None
    period_interval: int | None
    coverage_expected: int | None
    coverage_observed: int | None
    coverage_pct: float | None
    dedup_key: str


class FailedSensor(TypedDict):
    """Structured information about a failed sensor request."""

    sensor_id: int
    station_id: int
    pollutant: str
    error_type: str
    error_message: str

@dataclass
class PollingResult:
    """Accumulated metrics and data from polling all sensors."""
    rows: list[HourRow] = field(default_factory=list)
    sensors_with_data: set[int] = field(default_factory=set)
    stations_with_data: set[int] = field(default_factory=set)
    failed_sensors: list[FailedSensor] = field(default_factory=list)
    api_calls: int = 0
    api_errors: int = 0
    sensors_queried: int = 0


class PollerSummary(TypedDict):
    """Summary returned by run_poller()."""

    source: str
    run_id: str
    ingestion_timestamp: str
    record_count: int
    station_count: int
    sensors_queried: int
    sensors_with_data: int
    data_timestamp_min: str | None
    data_timestamp_max: str | None
    bq_table: str
    status: str
    error_message: str | None
    failed_sensor_count: int
    api_calls: int
    api_errors: int
    window_start_utc: str | None
    window_end_utc: str | None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_REQUIRED_POLLUTANTS: list[str] = ["no2", "pm10", "pm25"]
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Env-var helpers
# ---------------------------------------------------------------------------


def _parse_bool_env(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "t", "yes", "y"}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    project_id: str
    raw_dataset: str
    station_sensors_table: str
    hourly_table: str
    bq_location: str
    openaq_base_url: str
    openaq_api_key: str | None
    lookback_hours: int
    max_workers: int
    http_timeout_seconds: int
    dev_station_ids: list[str]
    max_sensors: int | None
    enforce_complete_hours: bool
    required_pollutants: list[str]
    openaq_rate_limit_per_minute: int
    openaq_rate_limit_per_hour: int
    max_http_attempts: int
    progress_log_every: int
    progress_log_interval_seconds: int

    @classmethod
    def from_env(cls) -> Config:
        project_id = os.getenv("PROJECT_ID")
        if not project_id:
            raise ValueError("Could not determine GCP project ID.")

        return cls(
            project_id=project_id,
            raw_dataset=os.getenv(
                "BQ_RAW_DATASET", "air_quality_raw"
            ),
            station_sensors_table=os.getenv(
                "BQ_STATION_SENSORS_TABLE", "station_sensors"
            ),
            hourly_table=os.getenv("BQ_OPENAQ_HOURLY_TABLE", "openaq_hourly"),
            bq_location=os.getenv("BQ_LOCATION", "EU"),
            openaq_base_url=os.getenv(
                "OPENAQ_BASE_URL", "https://api.openaq.org/v3"
            ).rstrip("/"),
            openaq_api_key=os.getenv("OPENAQ_API_KEY", "48193f5896bc7163a8dab4d9c3f2ab5ad263eeaade7b0b2ccfa8906cc76ed968")
            or None,
            lookback_hours=int(os.getenv("LOOKBACK_HOURS", "3")),
            max_workers=int(os.getenv("MAX_WORKERS", "8")),
            http_timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            dev_station_ids=parse_csv_env(os.getenv("DEV_STATION_IDS")),
            max_sensors=parse_optional_int(os.getenv("MAX_SENSORS")),
            enforce_complete_hours=_parse_bool_env(
                os.getenv("ENFORCE_COMPLETE_HOURS"), default=True
            ),
            required_pollutants=parse_csv_env(os.getenv("TARGET_POLLUTANTS", DEFAULT_REQUIRED_POLLUTANTS)),
            openaq_rate_limit_per_minute=int(
                os.getenv("OPENAQ_RATE_LIMIT_PER_MINUTE", "60")
            ),
            openaq_rate_limit_per_hour=int(
                os.getenv("OPENAQ_RATE_LIMIT_PER_HOUR", "2000")
            ),
            max_http_attempts=int(os.getenv("MAX_HTTP_ATTEMPTS", "5")),
            progress_log_every=int(os.getenv("PROGRESS_LOG_EVERY", "25")),
            progress_log_interval_seconds=int(
                os.getenv("PROGRESS_LOG_INTERVAL_SECONDS", "30")
            ),
        )


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------


def get_query_window(
    now: datetime, lookback_hours: int
) -> tuple[datetime, datetime]:
    """Return (start, end) covering the last *lookback_hours* complete
    clock hours.  Example at 10:05 UTC with lookback=3 → (07:00, 10:00).
    """
    window_end = now.replace(minute=0, second=0, microsecond=0)
    window_start = window_end - timedelta(hours=lookback_hours)
    return window_start, window_end


# ---------------------------------------------------------------------------
# BigQuery – load station sensors
# ---------------------------------------------------------------------------


def load_station_sensors(
    config: Config, bq_client: bigquery.Client
) -> list[Sensor]:
    """Return the list of sensors to poll.

    Only stations that report **all** required pollutants are included.
    """
    sql = f"""
        WITH eligible_stations AS (
          SELECT station_id
          FROM `{config.project_id}.{config.raw_dataset}.{config.station_sensors_table}`
          WHERE parameter_name IN UNNEST(@pollutants)
          GROUP BY station_id
          HAVING COUNT(DISTINCT parameter_name) = @pollutant_count
        )
        SELECT
          s.station_id,
          s.openaq_location_id,
          s.openaq_sensor_id,
          s.parameter_name  AS pollutant,
          s.parameter_units AS unit
        FROM `{config.project_id}.{config.raw_dataset}.{config.station_sensors_table}` s
        JOIN eligible_stations e USING (station_id)
        WHERE s.parameter_name IN UNNEST(@pollutants)
    """

    query_parameters: list[
        bigquery.ScalarQueryParameter | bigquery.ArrayQueryParameter
    ] = [
        bigquery.ArrayQueryParameter(
            "pollutants", "STRING", config.required_pollutants
        ),
        bigquery.ScalarQueryParameter(
            "pollutant_count", "INT64", len(config.required_pollutants)
        ),
    ]

    if config.dev_station_ids:
        sql += "\nAND s.station_id IN UNNEST(@station_ids)"
        query_parameters.append(
            bigquery.ArrayQueryParameter(
                "station_ids", "STRING", config.dev_station_ids
            )
        )

    sql += "\nORDER BY s.station_id, pollutant, openaq_sensor_id"

    job_config = bigquery.QueryJobConfig(query_parameters=query_parameters)
    rows = [
        dict(row)
        for row in bq_client.query(
            sql, job_config=job_config, location=config.bq_location
        ).result()
    ]

    if config.max_sensors is not None:
        rows = rows[: config.max_sensors]

    logging.info(
        "Loaded %s sensors from BigQuery  pollutants=%s",
        len(rows),
        config.required_pollutants,
    )
    return rows


# ---------------------------------------------------------------------------
# BigQuery – append rows
# ---------------------------------------------------------------------------


def _append_rows_to_bigquery(
    config: Config,
    bq_client: bigquery.Client,
    rows: list[HourRow],
) -> int:
    """Write rows to BigQuery via load_table_from_json.

    Returns the number of rows written.
    """
    if not rows:
        logging.info("No rows to write to BigQuery rows=0")
        return 0

    table_id = (
        f"{config.project_id}.{config.raw_dataset}.{config.hourly_table}"
    )
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    load_job = bq_client.load_table_from_json(
        rows,
        table_id,
        job_config=job_config,
        location=config.bq_location,
    )
    load_job.result()
    logging.info("Appended rows=%d to=%s", len(rows), table_id)
    return len(rows)


# ---------------------------------------------------------------------------
# Transform a single hour payload
# ---------------------------------------------------------------------------


def _transform_hour_row(
    sensor: Sensor,
    hour_payload: HourPayload,
    ingested_at: str,
    run_id: str,
    window_end: datetime,
    enforce_complete_hours: bool,
) -> HourRow | None:
    period = hour_payload["period"]
    coverage = hour_payload["coverage"]

    period_from_utc = deep_get(period, "datetimeFrom", "utc")
    period_to_utc = deep_get(period, "datetimeTo", "utc")
    period_from_local = deep_get(period, "datetimeFrom", "local")
    value = hour_payload.get("value")

    if period_from_utc is None or period_to_utc is None or value is None:
        logging.warning(
            "Skipping malformed hour payload for sensor %s: %s",
            sensor["openaq_sensor_id"],
            hour_payload,
        )
        return None

    period_to_dt = parse_timestamp(period_to_utc)
    if enforce_complete_hours and period_to_dt and period_to_dt > window_end:
        return None

    return {
        "ingested_at": ingested_at,
        "run_id": run_id,
        "station_id": sensor["station_id"],
        "openaq_location_id": sensor["openaq_location_id"],
        "openaq_sensor_id": sensor["openaq_sensor_id"],
        "pollutant": sensor["pollutant"],
        "value": value,
        "unit": sensor.get("unit"),
        "period_from_utc": period_from_utc,
        "period_to_utc": period_to_utc,
        "period_from_local": period_from_local,
        "period_label": period.get("label"),
        "period_interval": period.get("interval"),
        "coverage_expected": coverage.get("expectedCount"),
        "coverage_observed": coverage.get("observedCount"),
        "coverage_pct": coverage.get("percentComplete"),
        "dedup_key": f"{sensor['openaq_sensor_id']}|{period_from_utc}",
    }


# ---------------------------------------------------------------------------
# Fetch one sensor (with manual retries + rate limiting)
# ---------------------------------------------------------------------------


def _fetch_sensor_hours(
    config: Config,
    sensor: Sensor,
    window_start: datetime,
    window_end: datetime,
    ingested_at: str,
    run_id: str,
    rate_limiter: DualWindowRateLimiter,
    progress: ProgressTracker,
    session : requests.Session
) -> list[HourRow]:
    sensor_id = sensor["openaq_sensor_id"]

    url = f"{config.openaq_base_url}/sensors/{sensor_id}/hours"
    params = {
        "datetime_from": to_rfc3339_z(window_start),
        "datetime_to": to_rfc3339_z(window_end),
        "limit": 24,
        "page": 1,
    }

    for attempt in range(1, config.max_http_attempts + 1):
        rate_limiter.acquire()
        progress.record_http_attempt()

        try:
            response = session.get(
                url, params=params, timeout=config.http_timeout_seconds
            )

            # ---- retryable HTTP status ----
            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt == config.max_http_attempts:
                    response.raise_for_status()

                progress.record_retry()
                delay = backoff_seconds(attempt, response)
                logging.warning(
                    "Retryable %s run_id=%s  sensor_id=%s  attempt=%s/%s  wait=%.1fs",
                    run_id,
                    response.status_code,
                    sensor_id,
                    attempt,
                    config.max_http_attempts,
                    delay,
                )
                time.sleep(delay)
                continue

            response.raise_for_status()

            # ---- success path ----
            results = response.json().get("results", [])
            transformed: list[HourRow] = []

            for item in results:
                row = _transform_hour_row(
                    sensor=sensor,
                    hour_payload=item,
                    ingested_at=ingested_at,
                    run_id=run_id,
                    window_end=window_end,
                    enforce_complete_hours=config.enforce_complete_hours,
                )
                if row is not None:
                    transformed.append(row)

            return transformed

        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == config.max_http_attempts:
                raise
            progress.record_retry()
            delay = backoff_seconds(attempt)
            logging.warning(
                "Network error run_id=%s sensor_id=%s  attempt=%s/%s  error=%s  wait=%.1fs",
                run_id,
                sensor_id,
                attempt,
                config.max_http_attempts,
                exc,
                delay,
            )
            time.sleep(delay)

        except requests.HTTPError:
            raise

        except ValueError as exc:
            if attempt == config.max_http_attempts:
                raise
            progress.record_retry()
            delay = backoff_seconds(attempt)
            logging.warning(
                "Invalid JSON run_id=%s sensor_id=%s  attempt=%s/%s  error=%s  wait=%.1fs",
                run_id,
                sensor_id,
                attempt,
                config.max_http_attempts,
                exc,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError(
        f"Exhausted {config.max_http_attempts} attempts for sensor_id={sensor_id}"
    )


# ---------------------------------------------------------------------------
# Main poller orchestrator
# ---------------------------------------------------------------------------

def _collect_future_result(
    future,
    sensor: Sensor,
    result: PollingResult,
    progress: ProgressTracker,
) -> None:
    """Handle one completed future, updating *result* in place."""
    sensor_id = sensor["openaq_sensor_id"]

    try:
        rows = future.result()
        result.api_calls += 1
    except Exception as exc:
        result.api_errors += 1
        result.failed_sensors.append(
            {
                "sensor_id": sensor_id,
                "station_id": sensor["station_id"],
                "pollutant": sensor["pollutant"],
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        progress.record_failure()
        logging.exception(
            "Failed  sensor_id=%s  station_id=%s  pollutant=%s  error=%s",
            sensor_id, sensor["station_id"], sensor["pollutant"], exc,
        )
        return

    progress.record_success(rows_count=len(rows), had_data=bool(rows))

    if rows:
        result.rows.extend(rows)
        result.sensors_with_data.add(sensor_id)
        for row in rows:
            result.stations_with_data.add(row["station_id"])

def _poll_sensors(
    config: Config,
    bq_client: bigquery.Client,
    run_id: str,
    ingested_at: str,
    window_start: datetime,
    window_end: datetime,
) -> PollingResult:
    """Load sensors and fetch hourly data for each one concurrently."""
    result = PollingResult()

    sensors = load_station_sensors(config, bq_client)
    result.sensors_queried = len(sensors)

    if not sensors:
        return result

    session = get_session(
        pool_connections=config.max_workers,
        pool_maxsize=config.max_workers,
        extra_headers={"X-API-Key": config.openaq_api_key}
        if config.openaq_api_key
        else None,
    )
    rate_limiter = DualWindowRateLimiter(
        per_minute=config.openaq_rate_limit_per_minute,
        per_hour=config.openaq_rate_limit_per_hour,
    )
    progress = ProgressTracker(
        run_id=run_id,
        total_items=result.sensors_queried,
        log_every=config.progress_log_every,
        log_interval_seconds=config.progress_log_interval_seconds,
    )
    progress.start()

    worker_count = min(config.max_workers, max(1, result.sensors_queried))
    logging.info(
        "Querying %s sensors  workers=%s  rate_limit=%s/min %s/hour",
        result.sensors_queried, worker_count,
        config.openaq_rate_limit_per_minute,
        config.openaq_rate_limit_per_hour,
    )

    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_sensor = {
                executor.submit(
                    _fetch_sensor_hours,
                    config, sensor, window_start, window_end,
                    ingested_at, run_id, rate_limiter, progress, session,
                ): sensor
                for sensor in sensors
            }

            for future in as_completed(future_to_sensor):
                sensor = future_to_sensor[future]
                _collect_future_result(future, sensor, result, progress)
    finally:
        progress.stop()

    return result

def _persist_rows(
    config: Config,
    bq_client: bigquery.Client,
    rows: list[HourRow],
) -> tuple[int, str | None]:
    """Write rows to BigQuery. Returns (records_written, error_string|None)."""
    if not rows:
        return 0, None
    try:
        written = _append_rows_to_bigquery(config, bq_client, rows)
        return written, None
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logging.exception("BigQuery write failed: %s", error)
        return 0, error

def _determine_status(
    polling: PollingResult,
    bq_write_error: str | None,
    unhandled_exception: Exception | None,
) -> tuple[str, str | None]:
    """Derive the run status and optional error message."""
    if unhandled_exception is not None:
        return "error", f"{type(unhandled_exception).__name__}: {unhandled_exception}"
    if bq_write_error is not None:
        return "error", bq_write_error
    if not polling.rows:
        return "empty", None
    if polling.failed_sensors:
        return "partial_success", None
    return "success", None

def _log_run(
    bq_client: bigquery.Client,
    config: Config,
    run_id: str,
    run_started_at: datetime,
    run_finished_at: datetime,
    window_start: datetime,
    window_end: datetime,
    records_written: int,
    polling: PollingResult,
    status: str,
    error_message: str | None,
) -> None:
    """Persist an ingestion-log row to BigQuery."""
    failed_sensor_id_list = [
        str(fs["sensor_id"]) for fs in polling.failed_sensors
    ]

    write_ingestion_log(
        bq_client=bq_client,
        project_id=config.project_id,
        raw_dataset=config.raw_dataset,
        run_id=run_id,
        source="openaq",
        status=status,
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
        records_written=records_written,
        sensors_queried=polling.sensors_queried,
        sensors_failed=len(polling.failed_sensors),
        stations_polled=None,
        stations_failed=None,
        api_calls=polling.api_calls,
        api_errors=polling.api_errors,
        window_start=window_start,
        window_end=window_end,
        error_message=error_message,
        failed_sensor_ids=failed_sensor_id_list or None,
        failed_station_ids=None,
    )

def _build_summary(
    run_id: str,
    ingested_at: str,
    config: Config,
    window_start: datetime,
    window_end: datetime,
    polling: PollingResult,
    records_written: int,
    status: str,
    error_message: str | None,
) -> PollerSummary:
    """Assemble the final summary dict returned to the caller."""
    timestamps = [
        r["period_from_utc"] for r in polling.rows if r.get("period_from_utc")
    ]

    return {
        "source": "openaq",
        "run_id": run_id,
        "ingestion_timestamp": ingested_at,
        "record_count": records_written,
        "station_count": len(polling.stations_with_data),
        "sensors_queried": polling.sensors_queried,
        "sensors_with_data": len(polling.sensors_with_data),
        "data_timestamp_min": min(timestamps) if timestamps else None,
        "data_timestamp_max": max(timestamps) if timestamps else None,
        "bq_table": f"{config.raw_dataset}.{config.hourly_table}",
        "status": status,
        "error_message": error_message,
        "failed_sensor_count": len(polling.failed_sensors),
        "api_calls": polling.api_calls,
        "api_errors": polling.api_errors,
        "window_start_utc": to_rfc3339_z(window_start),
        "window_end_utc": to_rfc3339_z(window_end),
    }


def run_poller(config: Config, bq_client: bigquery.Client) -> PollerSummary:
    """Run the OpenAQ poller and return a summary dict."""
    run_started_at = utc_now()
    ingested_at = to_rfc3339_z(run_started_at)
    run_id = build_run_id(run_started_at)
    window_start, window_end = get_query_window(
        run_started_at, config.lookback_hours
    )

    logging.info(
        "Starting poller  run_id=%s  window=[%s, %s)",
        run_id, to_rfc3339_z(window_start), to_rfc3339_z(window_end),
    )

    polling = PollingResult()
    records_written = 0
    bq_write_error: str | None = None
    unhandled_exception: Exception | None = None

    try:
        polling = _poll_sensors(
            config, bq_client, run_id, ingested_at, window_start, window_end,
        )
        records_written, bq_write_error = _persist_rows(
            config, bq_client, polling.rows,
        )
    except Exception as exc:
        unhandled_exception = exc
        logging.exception("Unhandled exception in run_poller: %s", exc)
    finally:
        run_finished_at = utc_now()
        status, error_message = _determine_status(
            polling, bq_write_error, unhandled_exception,
        )
        _log_run(
            bq_client, config, run_id,
            run_started_at, run_finished_at,
            window_start, window_end,
            records_written, polling, status, error_message,
        )

    summary = _build_summary(
        run_id, ingested_at, config,
        window_start, window_end,
        polling, records_written, status, error_message,
    )
    logging.info("Completed run  summary=%s", summary)
    return summary   


# ---------------------------------------------------------------------------
# Local dev entry point
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        config = Config.from_env()
        bq_client = bigquery.Client(
            project=config.project_id, location=config.bq_location
        )
        summary = run_poller(config, bq_client)
        if summary["status"] not in ("success", "empty"):
            logging.warning("OpenAQ poller completed with status=%s", summary["status"])
            return 1

        logging.info("OpenAQ poller finished  summary=%s", summary)
        return 0

    except Exception as exc:
        logging.exception("OpenAQ poller failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())