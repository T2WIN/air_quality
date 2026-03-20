"""OpenAQ hourly-data poller – Cloud Run job.

Fetches pre-aggregated hourly measurements from the OpenAQ v3 API for
every sensor registered in the ``station_sensors`` lookup table if the sensor belongs to a station that aggregates all the required pollutants, then
appends the rows to a BigQuery raw table.
"""

import json
import logging
import os
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import requests
from google.cloud import bigquery
from requests.adapters import HTTPAdapter

from progress_tracker import ProgressTracker
from rate_limiter import DualWindowRateLimiter

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

DEFAULT_REQUIRED_POLLUTANTS: List[str] = ["no2", "pm10", "pm25"]
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_thread_local = threading.local()
bq_client = bigquery.Client()

# ---------------------------------------------------------------------------
# Env-var helpers
# ---------------------------------------------------------------------------


def _parse_csv_env(name: str) -> List[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
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
    openaq_base_url: str
    openaq_api_key: Optional[str]
    lookback_hours: int
    max_workers: int
    http_timeout_seconds: int
    dev_station_ids: List[str]
    max_sensors: Optional[int]
    enforce_complete_hours: bool
    required_pollutants: List[str]
    openaq_rate_limit_per_minute: int
    openaq_rate_limit_per_hour: int
    max_http_attempts: int
    progress_log_every: int
    progress_log_interval_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        project_id = (
            os.getenv("GCP_PROJECT")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or bq_client.project
        )
        if not project_id:
            raise ValueError("Could not determine GCP project ID.")

        return cls(
            project_id=project_id,
            raw_dataset=os.getenv(
                "BQ_RAW_DATASET", os.getenv("BQ_DATASET", "air_quality_raw")
            ),
            station_sensors_table=os.getenv(
                "BQ_STATION_SENSORS_TABLE", "station_sensors"
            ),
            hourly_table=os.getenv("BQ_OPENAQ_HOURLY_TABLE", "openaq_hourly"),
            openaq_base_url=os.getenv(
                "OPENAQ_BASE_URL", "https://api.openaq.org/v3"
            ).rstrip("/"),
            openaq_api_key=(os.getenv("OPENAQ_API_KEY", "") or "").strip()
            or None,
            lookback_hours=int(os.getenv("LOOKBACK_HOURS", "3")),
            max_workers=int(os.getenv("MAX_WORKERS", "8")),
            http_timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            dev_station_ids=_parse_csv_env("DEV_STATION_IDS"),
            max_sensors=_parse_optional_int(os.getenv("MAX_SENSORS")),
            enforce_complete_hours=_parse_bool_env(
                "ENFORCE_COMPLETE_HOURS", default=True
            ),
            required_pollutants=_parse_csv_env("TARGET_POLLUTANTS")
            or DEFAULT_REQUIRED_POLLUTANTS,
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_rfc3339_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def deep_get(obj: Dict[str, Any], *keys: str) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def build_run_id(run_started_at: datetime) -> str:
    return f"{run_started_at.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"


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
# HTTP session (one per thread, no automatic retries)
# ---------------------------------------------------------------------------


def _get_session(config: Config) -> requests.Session:
    if not hasattr(_thread_local, "session"):
        session = requests.Session()

        adapter = HTTPAdapter(
            max_retries=0,
            pool_connections=config.max_workers,
            pool_maxsize=config.max_workers,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        headers: Dict[str, str] = {"Accept": "application/json"}
        if config.openaq_api_key:
            headers["X-API-Key"] = config.openaq_api_key
        session.headers.update(headers)

        _thread_local.session = session

    return _thread_local.session


# ---------------------------------------------------------------------------
# Retry / back-off helpers
# ---------------------------------------------------------------------------


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse an HTTP ``Retry-After`` header (seconds or HTTP-date)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(
            0.0, (dt.astimezone(timezone.utc) - utc_now()).total_seconds()
        )
    except Exception:
        return None


def _backoff_seconds(
    attempt: int, response: Optional[requests.Response] = None
) -> float:
    """Exponential back-off with jitter, honouring Retry-After if present."""
    if response is not None:
        retry_after = _parse_retry_after(
            response.headers.get("Retry-After")
        )
        if retry_after is not None:
            return retry_after
    base = min(2 ** (attempt - 1), 30)
    return base + random.uniform(0, 0.5)


# ---------------------------------------------------------------------------
# BigQuery – load station sensors
# ---------------------------------------------------------------------------


def load_station_sensors(config: Config) -> List[Dict[str, Any]]:
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

    query_parameters: List[bigquery.ScalarQueryParameter | bigquery.ArrayQueryParameter] = [
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
        for row in bq_client.query(sql, job_config=job_config).result()
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
    config: Config, rows: List[Dict[str, Any]]
) -> None:
    if not rows:
        logging.info("No rows to write to BigQuery")
        return

    table_id = f"{config.project_id}.{config.raw_dataset}.{config.hourly_table}"
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    load_job = bq_client.load_table_from_json(
        rows, table_id, job_config=job_config
    )
    load_job.result()
    logging.info("Appended %s rows to %s", len(rows), table_id)


# ---------------------------------------------------------------------------
# Ingestion log writer
# ---------------------------------------------------------------------------

INGESTION_LOG_TABLE = "ingestion_log"


def _write_ingestion_log(
    config: Config,
    run_id: str,
    source: str,
    status: str,
    run_started_at: datetime,
    run_finished_at: datetime,
    records_written: int,
    sensors_queried: Optional[int],
    sensors_failed: Optional[int],
    stations_polled: Optional[int],
    stations_failed: Optional[int],
    api_calls: int,
    api_errors: int,
    window_start: Optional[datetime],
    window_end: Optional[datetime],
    error_message: Optional[str],
    failed_sensor_ids: Optional[List[Dict[str, Any]]],
    failed_station_ids: Optional[List[str]],
) -> None:
    """Write a row to the ingestion_log table."""
    import json

    table_id = f"{config.project_id}.{config.raw_dataset}.{INGESTION_LOG_TABLE}"

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
        logging.info("Wrote ingestion_log row: run_id=%s status=%s records=%d",
                     run_id, status, records_written)
    except Exception as exc:
        logging.error("Failed to write ingestion_log row: %s", exc)


# ---------------------------------------------------------------------------
# Transform a single hour payload
# ---------------------------------------------------------------------------


def _transform_hour_row(
    sensor: Dict[str, Any],
    hour_payload: Dict[str, Any],
    ingested_at: datetime,
    run_id: str,
    window_end: datetime,
    enforce_complete_hours: bool,
) -> Optional[Dict[str, Any]]:
    period = hour_payload.get("period", {})
    coverage = hour_payload.get("coverage", {})

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
        "ingested_at": to_rfc3339_z(ingested_at),
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
    sensor: Dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    ingested_at: datetime,
    run_id: str,
    rate_limiter: DualWindowRateLimiter,
    progress: ProgressTracker,
) -> List[Dict[str, Any]]:
    session = _get_session(config)
    sensor_id = sensor["openaq_sensor_id"]

    url = f"{config.openaq_base_url}/sensors/{sensor_id}/hours"
    params = {
        "datetime_from": to_rfc3339_z(window_start),
        "datetime_to": to_rfc3339_z(window_end),
        "limit": 24,
        "page": 1,
    }

    for attempt in range(1, config.max_http_attempts + 1):
        response = None
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
                delay = _backoff_seconds(attempt, response)
                logging.warning(
                    "Retryable %s  sensor_id=%s  attempt=%s/%s  wait=%.1fs",
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
            transformed: List[Dict[str, Any]] = []

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
            delay = _backoff_seconds(attempt)
            logging.warning(
                "Network error  sensor_id=%s  attempt=%s/%s  error=%s  wait=%.1fs",
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
            delay = _backoff_seconds(attempt)
            logging.warning(
                "Invalid JSON  sensor_id=%s  attempt=%s/%s  error=%s  wait=%.1fs",
                sensor_id,
                attempt,
                config.max_http_attempts,
                exc,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError(f"Exhausted {config.max_http_attempts} attempts for sensor_id={sensor_id}")

# ---------------------------------------------------------------------------
# Main poller orchestrator
# ---------------------------------------------------------------------------


def run_poller(config: Config) -> Dict[str, Any]:
    run_started_at = utc_now()
    ingested_at = run_started_at
    run_id = build_run_id(run_started_at)
    window_start, window_end = get_query_window(
        run_started_at, config.lookback_hours
    )

    logging.info(
        "Starting poller  run_id=%s  window=[%s, %s)",
        run_id,
        to_rfc3339_z(window_start),
        to_rfc3339_z(window_end),
    )

    # ---- initialize tracking variables --------------------------------------
    all_rows: List[Dict[str, Any]] = []
    sensors_with_data: set[int] = set()
    stations_with_data: set[str] = set()
    failed_sensors: List[Dict[str, Any]] = []  # structured failure info
    api_calls = 0
    api_errors = 0
    bq_write_error: Optional[str] = None
    unhandled_exception: Optional[Exception] = None
    sensors_queried = 0

    try:
        # ---- load sensor list --------------------------------------------------
        sensors = load_station_sensors(config)
        sensors_queried = len(sensors)

        if sensors_queried == 0:
            _write_ingestion_log(
                config=config,
                run_id=run_id,
                source="openaq",
                status="empty",
                run_started_at=run_started_at,
                run_finished_at=utc_now(),
                records_written=0,
                sensors_queried=0,
                sensors_failed=0,
                stations_polled=None,
                stations_failed=None,
                api_calls=0,
                api_errors=0,
                window_start=window_start,
                window_end=window_end,
                error_message=None,
                failed_sensor_ids=None,
                failed_station_ids=None,
            )
            return _build_summary(
                run_id, ingested_at, config, window_start, window_end,
                record_count=0, station_count=0, sensors_queried=0,
                sensors_with_data=0, data_timestamp_min=None,
                data_timestamp_max=None, failed_sensor_count=0,
                api_calls=0, api_errors=0,
            )

        # ---- set up rate limiter + progress ------------------------------------
        rate_limiter = DualWindowRateLimiter(
            per_minute=config.openaq_rate_limit_per_minute,
            per_hour=config.openaq_rate_limit_per_hour,
        )

        progress = ProgressTracker(
            run_id=run_id,
            total_sensors=sensors_queried,
            log_every=config.progress_log_every,
            log_interval_seconds=config.progress_log_interval_seconds,
        )
        progress.start()

        worker_count = min(config.max_workers, max(1, sensors_queried))
        logging.info(
            "Querying %s sensors  workers=%s  rate_limit=%s/min %s/hour",
            sensors_queried,
            worker_count,
            config.openaq_rate_limit_per_minute,
            config.openaq_rate_limit_per_hour,
        )

        # ---- fan out -----------------------------------------------------------
        try:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_sensor = {
                    executor.submit(
                        _fetch_sensor_hours,
                        config,
                        sensor,
                        window_start,
                        window_end,
                        ingested_at,
                        run_id,
                        rate_limiter,
                        progress,
                    ): sensor
                    for sensor in sensors
                }

                for future in as_completed(future_to_sensor):
                    sensor = future_to_sensor[future]
                    sensor_id = sensor["openaq_sensor_id"]

                    try:
                        rows = future.result()
                        api_calls += 1
                    except Exception as exc:
                        api_errors += 1
                        failed_sensors.append({
                            "sensor_id": sensor_id,
                            "station_id": sensor["station_id"],
                            "pollutant": sensor["pollutant"],
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        })
                        progress.record_failure()
                        logging.exception(
                            "Failed  sensor_id=%s  station_id=%s  pollutant=%s  error=%s",
                            sensor_id,
                            sensor["station_id"],
                            sensor["pollutant"],
                            exc,
                        )
                        continue

                    progress.record_success(
                        rows_count=len(rows), had_data=bool(rows)
                    )

                    if rows:
                        all_rows.extend(rows)
                        sensors_with_data.add(sensor_id)
                        for row in rows:
                            stations_with_data.add(row["station_id"])
        finally:
            progress.stop()

        # ---- persist -----------------------------------------------------------
        try:
            _append_rows_to_bigquery(config, all_rows)
        except Exception as exc:
            bq_write_error = f"{type(exc).__name__}: {exc}"
            logging.error("BigQuery write failed: %s", bq_write_error)

    except Exception as exc:
        unhandled_exception = exc
        logging.exception("Unhandled exception in run_poller: %s", exc)

    finally:
        # ---- determine status ---------------------------------------------------
        run_finished_at = utc_now()

        if unhandled_exception is not None:
            status = "error"
            error_message = f"{type(unhandled_exception).__name__}: {unhandled_exception}"
        elif bq_write_error is not None:
            status = "error"
            error_message = bq_write_error
        elif len(all_rows) == 0:
            status = "empty"
            error_message = None
        elif failed_sensors:
            status = "partial_success"
            error_message = None
        else:
            status = "success"
            error_message = None

        timestamps = [
            r["period_from_utc"] for r in all_rows if r.get("period_from_utc")
        ]

        # ---- write ingestion log (always, even on failure) --------------------
        _write_ingestion_log(
            config=config,
            run_id=run_id,
            source="openaq",
            status=status,
            run_started_at=run_started_at,
            run_finished_at=run_finished_at,
            records_written=len(all_rows),
            sensors_queried=sensors_queried,
            sensors_failed=len(failed_sensors),
            stations_polled=None,
            stations_failed=None,
            api_calls=api_calls,
            api_errors=api_errors,
            window_start=window_start,
            window_end=window_end,
            error_message=error_message,
            failed_sensor_ids=failed_sensors if failed_sensors else None,
            failed_station_ids=None,
        )

    summary = _build_summary(
        run_id=run_id,
        ingested_at=ingested_at,
        config=config,
        window_start=window_start,
        window_end=window_end,
        record_count=len(all_rows),
        station_count=len(stations_with_data),
        sensors_queried=sensors_queried,
        sensors_with_data=len(sensors_with_data),
        data_timestamp_min=min(timestamps) if timestamps else None,
        data_timestamp_max=max(timestamps) if timestamps else None,
        failed_sensor_count=len(failed_sensors),
        api_calls=api_calls,
        api_errors=api_errors,
    )

    logging.info("Completed run  summary=%s", summary)
    return summary


def _build_summary(
    run_id: str,
    ingested_at: datetime,
    config: Config,
    window_start: datetime,
    window_end: datetime,
    *,
    record_count: int,
    station_count: int,
    sensors_queried: int,
    sensors_with_data: int,
    data_timestamp_min: Optional[str],
    data_timestamp_max: Optional[str],
    failed_sensor_count: int,
    api_calls: int,
    api_errors: int,
) -> Dict[str, Any]:
    if failed_sensor_count == 0 and record_count > 0:
        status = "success"
    elif failed_sensor_count > 0 and record_count > 0:
        status = "partial_success"
    elif record_count == 0 and sensors_queried > 0:
        status = "empty"
    else:
        status = "error"

    return {
        "source": "openaq",
        "run_id": run_id,
        "ingestion_timestamp": to_rfc3339_z(ingested_at),
        "record_count": record_count,
        "station_count": station_count,
        "sensors_queried": sensors_queried,
        "sensors_with_data": sensors_with_data,
        "data_timestamp_min": data_timestamp_min,
        "data_timestamp_max": data_timestamp_max,
        "bq_table": f"{config.raw_dataset}.{config.hourly_table}",
        "status": status,
        "failed_sensor_count": failed_sensor_count,
        "api_calls": api_calls,
        "api_errors": api_errors,
        "window_start_utc": to_rfc3339_z(window_start),
        "window_end_utc": to_rfc3339_z(window_end),
    }


# ---------------------------------------------------------------------------
# Local dev entry point
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        config = Config.from_env()
        summary = run_poller(config)
        if summary["status"] != "success":
            logging.warning("OpenAQ poller completed with status=%s", summary["status"])

        logging.info("OpenAQ poller finished  summary=%s", summary)
        return 0

    except Exception as exc:
        logging.exception("OpenAQ poller failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())