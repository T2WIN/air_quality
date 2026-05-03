"""
Open-Meteo weather forecast poller — Cloud Run Job.

Fetches hourly weather forecasts for all French monitoring stations
from the Open-Meteo Forecast API and loads them into BigQuery.

Triggered by Cloud Scheduler every 6 hours.
No HTTP server — runs to completion and exits.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypedDict

import requests
from google.cloud import bigquery

from ingestion.shared import (
    DualWindowRateLimiter,
    ProgressTracker,
    backoff_seconds,
    build_run_id,
    get_session,
    parse_optional_int,
    to_rfc3339_z,
    utc_now,
    write_ingestion_log,
)

# ---------------------------------------------------------------------------
# TypedDicts for complex data structures
# ---------------------------------------------------------------------------


class StationLocation(TypedDict):
    """Station location record from BigQuery station_metadata table."""

    station_id: int
    latitude: float
    longitude: float


class OpenMeteoResult(TypedDict):
    """Open-Meteo API response result for a single location."""

    latitude: float
    longitude: float
    hourly: dict[str, list[Any]]


class WeatherRow(TypedDict):
    """Transformed weather row ready for BigQuery insert."""

    run_id: str
    station_id: int
    latitude: float
    longitude: float
    valid_time: str
    ingested_at: str
    dedup_key: str
    temperature_2m: float | None
    relative_humidity_2m: float | None
    surface_pressure: float | None
    wind_speed_10m: float | None
    wind_direction_10m: float | None
    precipitation: float | None
    cloud_cover: float | None
    boundary_layer_height: float | None


@dataclass
class PollingResult:
    """Accumulated metrics and data from polling all stations."""

    rows: list[WeatherRow] = field(default_factory=list)
    api_calls: int = 0
    api_errors: int = 0
    failed_stations: list[FailedStation] = field(default_factory=list)
    stations_polled: int = 0
    stations_with_data: int = 0


class FailedStation(TypedDict):
    """Structured information about a failed station request."""

    station_id: str
    error_type: str
    error_message: str


class PollerSummary(TypedDict):
    """Summary returned by run_poller()."""

    source: str
    run_id: str
    ingestion_timestamp: str
    record_count: int
    stations_polled: int
    stations_with_data: int
    bq_table: str
    status: str
    error_message: str | None
    failed_station_count: int
    api_calls: int
    api_errors: int


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


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "cloud_cover",
    "boundary_layer_height",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    project_id: str
    raw_dataset: str
    weather_table: str
    station_metadata_table: str
    bq_location: str
    batch_size: int
    forecast_hours: int
    open_meteo_rate_limit_per_minute: int
    open_meteo_rate_limit_per_hour: int
    open_meteo_url: str
    http_timeout_seconds: int
    max_http_attempts: int
    max_batches: int | None
    progress_log_every: int
    progress_log_interval_seconds: int

    @classmethod
    def from_env(cls) -> Config:
        project_id = os.getenv("PROJECT_ID")
        if not project_id:
            raise ValueError("Could not determine GCP project ID.")

        return cls(
            project_id=project_id,
            raw_dataset=os.getenv("BQ_RAW_DATASET", "air_quality_raw"),
            weather_table=os.getenv("BQ_WEATHER_TABLE", "weather_forecasts"),
            station_metadata_table=os.getenv("BQ_STATION_METADATA_TABLE", "station_metadata"),
            bq_location=os.getenv("BQ_LOCATION", "EU"),
            batch_size=int(os.getenv("BATCH_SIZE", "50")),
            forecast_hours=int(os.getenv("FORECAST_HOURS", "48")),
            open_meteo_rate_limit_per_minute=int(
                os.getenv(
                    "OPEN_METEO_RATE_LIMIT_PER_MINUTE",
                    300,
                )
            ),
            open_meteo_rate_limit_per_hour=int(
                os.getenv(
                    "OPEN_METEO_RATE_LIMIT_PER_HOUR",
                    1000,
                )
            ),
            open_meteo_url=os.getenv("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast"),
            http_timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            max_http_attempts=int(os.getenv("MAX_HTTP_ATTEMPTS", "5")),
            max_batches=parse_optional_int(os.getenv("MAX_BATCHES")),
            progress_log_every=int(os.getenv("PROGRESS_LOG_EVERY", "5")),
            progress_log_interval_seconds=int(os.getenv("PROGRESS_LOG_INTERVAL_SECONDS", "30")),
        )


# ---------------------------------------------------------------------------
# Station locations
# ---------------------------------------------------------------------------


def load_station_locations(config: Config, bq_client: bigquery.Client) -> list[StationLocation]:
    """Load station coordinates from BigQuery metadata table."""
    query = f"""
        SELECT station_id, latitude, longitude
        FROM `{config.project_id}.{config.raw_dataset}.{config.station_metadata_table}`
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY station_id
    """
    rows = [dict(row) for row in bq_client.query(query, location=config.bq_location).result()]
    logging.info("Loaded station_locations=%d from metadata", len(rows))
    return rows  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Open-Meteo API
# ---------------------------------------------------------------------------


def _fetch_batch_with_retry(
    config: Config,
    latitudes: list[float],
    longitudes: list[float],
    rate_limiter: DualWindowRateLimiter,
    progress: ProgressTracker,
    session: requests.Session,
) -> list[OpenMeteoResult]:
    """Call Open-Meteo Forecast API with retry logic.

    Single location → API returns a dict → we wrap in a list.
    Multiple locations → API returns a list.
    """

    params: dict[str, object] = {
        "latitude": ",".join(f"{lat:.4f}" for lat in latitudes),
        "longitude": ",".join(f"{lon:.4f}" for lon in longitudes),
        "hourly": ",".join(HOURLY_VARIABLES),
        "forecast_hours": config.forecast_hours,
        "timezone": "UTC",
    }

    last_exception: Exception | None = None

    for attempt in range(1, config.max_http_attempts + 1):
        rate_limiter.acquire(count=len(latitudes))
        progress.record_http_attempt()

        try:
            response = session.get(
                config.open_meteo_url,
                params=params,  # type: ignore[arg-type]
                timeout=config.http_timeout_seconds,
            )

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt == config.max_http_attempts:
                    response.raise_for_status()

                progress.record_retry()
                sleep_time = backoff_seconds(attempt, response)
                logging.warning(
                    "Retryable %s  attempt=%s/%s  wait=%.1fs",
                    response.status_code,
                    attempt,
                    config.max_http_attempts,
                    sleep_time,
                )
                time.sleep(sleep_time)
                continue

            response.raise_for_status()
            data = response.json()
            return [data] if isinstance(data, dict) else data  # type: ignore[list-item]

        except requests.HTTPError:
            raise

        except requests.RequestException as exc:
            last_exception = exc
            if attempt < config.max_http_attempts:
                progress.record_retry()
                sleep_time = backoff_seconds(attempt)
                logging.warning(
                    "Network error attempt=%s/%s  error=%s  wait=%.1fs",
                    attempt,
                    config.max_http_attempts,
                    exc,
                    sleep_time,
                )
                time.sleep(sleep_time)
                continue
            raise

    raise last_exception or RuntimeError("Unexpected retry loop exit")


# ---------------------------------------------------------------------------
# Parse and transform
# ---------------------------------------------------------------------------


def parse_batch(
    api_results: list[OpenMeteoResult],
    station_ids: list[int],
    latitudes: list[float],
    longitudes: list[float],
    ingested_at: str,
    run_id: str,
) -> list[WeatherRow]:
    """Flatten API response into one dict per (location, valid_time) hour."""
    if len(api_results) != len(station_ids):
        logging.warning(
            "Result count=%d != location count=%d skipping_batch=true",
            len(api_results),
            len(station_ids),
        )
        return []

    rows: list[WeatherRow] = []
    for i, result in enumerate(api_results):
        station_id = station_ids[i]
        lat = latitudes[i]
        lon = longitudes[i]

        hourly = result.get("hourly", {})
        times = hourly.get("time", [])

        for j, time_str in enumerate(times):
            valid_time = f"{time_str}:00+00:00" if len(time_str) == 16 else time_str

            # CRITICAL: dedup_key must NOT include ingested_at for idempotency
            dedup_key = f"{station_id}|{valid_time}"

            row = {
                "run_id": run_id,  # ← add
                "station_id": station_id,
                "latitude": lat,
                "longitude": lon,
                "valid_time": valid_time,
                "ingested_at": ingested_at,
                "dedup_key": dedup_key,
            }
            for var in HOURLY_VARIABLES:
                vals = hourly.get(var, [])
                row[var] = vals[j] if j < len(vals) else None

            rows.append(row)  # type: ignore[arg-type]

    return rows


# ---------------------------------------------------------------------------
# BigQuery load
# ---------------------------------------------------------------------------


def _append_rows_to_bigquery(
    config: Config,
    bq_client: bigquery.Client,
    rows: list[WeatherRow],
) -> int:
    """Write rows to BigQuery via load_table_from_json.

    Returns the number of rows written.
    """
    if not rows:
        logging.info("No rows to write to BigQuery rows=0")
        return 0

    table_ref = f"{config.project_id}.{config.raw_dataset}.{config.weather_table}"

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    load_job = bq_client.load_table_from_json(
        rows,  # type: ignore[arg-type]
        table_ref,
        job_config=job_config,
        location=config.bq_location,
    )
    load_job.result()
    logging.info("Appended rows=%d to=%s", len(rows), table_ref)
    return len(rows)


# ---------------------------------------------------------------------------
# Poller orchestrator
# ---------------------------------------------------------------------------


def _poll_stations(
    config: Config,
    bq_client: bigquery.Client,
    rate_limiter: DualWindowRateLimiter,
    ingested_at: str,
    run_id: str,
) -> PollingResult:
    """Load stations and poll them all in batches."""
    result = PollingResult()

    stations = load_station_locations(config, bq_client)
    if not stations:
        return result

    session = get_session()
    progress = ProgressTracker(
        run_id=run_id,
        total_items=len(stations),
        log_every=config.progress_log_every,
        log_interval_seconds=config.progress_log_interval_seconds,
    )
    progress.start()

    try:
        for batch_num, batch_start in enumerate(
            range(0, len(stations), config.batch_size), start=1
        ):
            if config.max_batches is not None and batch_num > config.max_batches:
                logging.info("Reached max_batches limit, stopping")
                break

            batch = stations[batch_start : batch_start + config.batch_size]
            _poll_batch(
                config,
                batch,  # type: ignore[arg-type]
                rate_limiter,
                progress,
                session,
                ingested_at,
                result,
                batch_num,
                run_id,
            )
    finally:
        progress.stop()

    return result


def _poll_batch(
    config: Config,
    batch: list[dict[str, Any]],
    rate_limiter: DualWindowRateLimiter,
    progress: ProgressTracker,
    session: requests.Session,
    ingested_at: str,
    result: PollingResult,
    batch_num: int,
    run_id: str,
) -> None:
    """Fetch and parse a single batch, updating *result* in place."""
    station_ids = [s["station_id"] for s in batch]
    latitudes = [s["latitude"] for s in batch]
    longitudes = [s["longitude"] for s in batch]

    try:
        api_results = _fetch_batch_with_retry(
            config,
            latitudes,
            longitudes,
            rate_limiter,
            progress,
            session,
        )
        rows = parse_batch(api_results, station_ids, latitudes, longitudes, ingested_at, run_id)
        result.rows.extend(rows)
        result.stations_with_data += len(api_results)
        result.api_calls += 1

        for _ in batch:
            progress.record_success(
                rows_count=len(rows) // max(len(batch), 1),
                had_data=bool(rows),
            )
    except Exception as exc:
        logging.exception("Batch %d failed: %s", batch_num, exc)
        result.api_errors += 1
        for station in batch:
            result.failed_stations.append(
                {
                    "station_id": station["station_id"],
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
            progress.record_failure()

    result.stations_polled += len(batch)


def _persist_rows(
    config: Config,
    bq_client: bigquery.Client,
    rows: list[WeatherRow],
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
    if polling.failed_stations:
        return "partial_success", None
    return "success", None


def _log_run(
    bq_client: bigquery.Client,
    config: Config,
    run_id: str,
    run_started_at: datetime,
    run_finished_at: datetime,
    records_written: int,
    polling: PollingResult,
    status: str,
    error_message: str | None,
) -> None:
    """Persist an ingestion-log row to BigQuery."""
    write_ingestion_log(
        bq_client=bq_client,
        project_id=config.project_id,
        raw_dataset=config.raw_dataset,
        run_id=run_id,
        source="open-meteo",
        status=status,
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
        records_written=records_written,
        sensors_queried=None,
        sensors_failed=None,
        stations_polled=polling.stations_polled,
        stations_failed=len(polling.failed_stations),
        api_calls=polling.api_calls,
        api_errors=polling.api_errors,
        window_start=None,
        window_end=None,
        error_message=error_message,
        failed_sensor_ids=None,
        failed_station_ids=[s["station_id"] for s in polling.failed_stations] or None,
    )


def _build_summary(
    run_id: str,
    ingested_at: str,
    config: Config,
    polling: PollingResult,
    records_written: int,
    status: str,
    error_message: str | None,
) -> PollerSummary:
    """Build the final summary dict returned to the caller."""
    return {
        "source": "open-meteo",
        "run_id": run_id,
        "ingestion_timestamp": ingested_at,
        "record_count": records_written,
        "stations_polled": polling.stations_polled,
        "stations_with_data": polling.stations_with_data,
        "bq_table": f"{config.raw_dataset}.{config.weather_table}",
        "status": status,
        "error_message": error_message,
        "failed_station_count": len(polling.failed_stations),
        "api_calls": polling.api_calls,
        "api_errors": polling.api_errors,
    }


def run_poller(config: Config, bq_client: bigquery.Client) -> PollerSummary:
    """Run the weather poller and return a summary dict."""
    run_started_at = utc_now()
    ingested_at = to_rfc3339_z(run_started_at)
    run_id = build_run_id(run_started_at)
    logging.info("Starting poller  run_id=%s", run_id)

    polling = PollingResult()
    records_written = 0
    bq_write_error: str | None = None
    unhandled_exception: Exception | None = None

    rate_limiter = DualWindowRateLimiter(
        per_minute=config.open_meteo_rate_limit_per_minute,
        per_hour=config.open_meteo_rate_limit_per_hour,
    )

    try:
        polling = _poll_stations(
            config,
            bq_client,
            rate_limiter,
            ingested_at,
            run_id,
        )
        records_written, bq_write_error = _persist_rows(
            config,
            bq_client,
            polling.rows,
        )
    except Exception as exc:
        unhandled_exception = exc
        logging.exception("Unhandled exception in weather poller: %s", exc)
    finally:
        run_finished_at = utc_now()
        status, error_message = _determine_status(
            polling,
            bq_write_error,
            unhandled_exception,
        )
        _log_run(
            bq_client,
            config,
            run_id,
            run_started_at,
            run_finished_at,
            records_written,
            polling,
            status,
            error_message,
        )

    summary = _build_summary(
        run_id,
        ingested_at,
        config,
        polling,
        records_written,
        status,
        error_message,
    )
    logging.info("Weather poller complete: summary=%s", summary)
    return summary


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Thin wrapper around run_poller()."""
    try:
        config = Config.from_env()
        bq_client = bigquery.Client(project=config.project_id, location=config.bq_location)
        summary = run_poller(config, bq_client)

        if summary["status"] not in ("success", "empty"):
            logging.warning("Weather poller completed with status=%s", summary["status"])
            return 1

        logging.info("Weather poller finished  summary=%s", summary)
        return 0

    except Exception as exc:
        logging.exception("Weather poller failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
