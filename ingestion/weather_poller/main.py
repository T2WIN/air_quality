"""
Open-Meteo weather forecast poller — Cloud Run Job.

Fetches hourly weather forecasts for all French monitoring stations
from the Open-Meteo Forecast API and loads them into BigQuery.

Triggered by Cloud Scheduler every 6 hours.
No HTTP server — runs to completion and exits.
"""

import io
import json
import logging
import os
import time
from datetime import datetime, timezone

import requests
from google.cloud import bigquery

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ID = os.environ["PROJECT_ID"]
BQ_DATASET = os.environ.get("BQ_RAW_DATASET", "air_quality_raw")
BQ_TABLE = os.environ.get("BQ_WEATHER_TABLE", "weather_forecasts")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "50"))
FORECAST_HOURS = int(os.environ.get("FORECAST_HOURS", "48"))

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Station locations
# ---------------------------------------------------------------------------

def get_station_locations(bq_client: bigquery.Client) -> list:
    """Load station coordinates from BigQuery metadata table."""
    query = f"""
        SELECT station_id, latitude, longitude
        FROM `{PROJECT_ID}.{BQ_DATASET}.station_metadata`
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY station_id
    """
    rows = list(bq_client.query(query, location="EU").result())
    logger.info("Loaded %d station locations from metadata", len(rows))
    return rows

# ---------------------------------------------------------------------------
# Open-Meteo API
# ---------------------------------------------------------------------------

def fetch_batch(latitudes: list[float], longitudes: list[float]) -> list[dict]:
    """
    Call Open-Meteo Forecast API for a batch of coordinates.

    Single location  → API returns a dict  → we wrap in a list.
    Multiple locations → API returns a list.
    """
    params = {
        "latitude": ",".join(f"{lat:.4f}" for lat in latitudes),
        "longitude": ",".join(f"{lon:.4f}" for lon in longitudes),
        "hourly": ",".join(HOURLY_VARIABLES),
        "forecast_hours": FORECAST_HOURS,
        "timezone": "UTC",
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, dict):
        return [data]
    return data


def parse_batch(
    api_results: list[dict],
    station_ids: list[int],
    latitudes: list[float],
    longitudes: list[float],
    ingested_at: str,
) -> list[dict]:
    """Flatten API response into one dict per (location, valid_time) hour."""
    if len(api_results) != len(station_ids):
        logger.warning(
            "Result count %d != location count %d — skipping batch",
            len(api_results),
            len(station_ids),
        )
        return []

    rows = []
    for i, result in enumerate(api_results):
        loc_id = station_ids[i]
        lat = latitudes[i]
        lon = longitudes[i]

        hourly = result.get("hourly", {})
        times = hourly.get("time", [])

        for j, t in enumerate(times):
            # Open-Meteo returns "2024-01-15T00:00" (UTC, no tz suffix)
            valid_time = f"{t}:00+00:00" if len(t) == 16 else t

            row = {
                "station_id": loc_id,
                "latitude": lat,
                "longitude": lon,
                "forecast_time": ingested_at,
                "valid_time": valid_time,
                "ingested_at": ingested_at,
                "dedup_key": f"{loc_id}|{valid_time}|{ingested_at}",
            }
            for var in HOURLY_VARIABLES:
                vals = hourly.get(var, [])
                row[var] = vals[j] if j < len(vals) else None

            rows.append(row)

    return rows

# ---------------------------------------------------------------------------
# BigQuery load
# ---------------------------------------------------------------------------

def load_to_bigquery(bq_client: bigquery.Client, rows: list[dict]) -> int:
    """Write rows to BigQuery via a batch load job (free)."""
    table_ref = f"{PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"

    ndjson = "\n".join(json.dumps(row) for row in rows)

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    load_job = bq_client.load_table_from_file(
        io.BytesIO(ndjson.encode("utf-8")),
        table_ref,
        job_config=job_config,
        location="EU",
    )
    load_job.result()  # block until done
    logger.info(
        "Loaded %d rows into %s (job %s)",
        load_job.output_rows,
        table_ref,
        load_job.job_id,
    )
    return load_job.output_rows


# ---------------------------------------------------------------------------
# Ingestion log writer
# ---------------------------------------------------------------------------

INGESTION_LOG_TABLE = "ingestion_log"


def _write_ingestion_log(
    bq_client: bigquery.Client,
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
    window_start_utc: str | None,
    window_end_utc: str | None,
    error_message: str | None,
    failed_sensor_ids: list | None,
    failed_station_ids: list | None,
) -> None:
    """Write a row to the ingestion_log table."""
    table_id = f"{PROJECT_ID}.{BQ_DATASET}.{INGESTION_LOG_TABLE}"

    # Convert datetime to RFC3339 string for JSON serialization
    run_started_at_str = run_started_at.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    run_finished_at_str = run_finished_at.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    row = {
        "run_id": run_id,
        "source": source,
        "status": status,
        "run_started_at": run_started_at_str,
        "run_finished_at": run_finished_at_str,
        "duration_seconds": (run_finished_at - run_started_at).total_seconds(),
        "records_written": records_written,
        "sensors_queried": sensors_queried,
        "sensors_failed": sensors_failed,
        "stations_polled": stations_polled,
        "stations_failed": stations_failed,
        "api_calls": api_calls,
        "api_errors": api_errors,
        "window_start_utc": window_start_utc,
        "window_end_utc": window_end_utc,
        "error_message": error_message,
        "failed_sensor_ids": json.dumps(failed_sensor_ids) if failed_sensor_ids else None,
        "failed_station_ids": json.dumps(failed_station_ids) if failed_station_ids else None,
        "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
    }

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    try:
        bq_client.load_table_from_json([row], table_id, job_config=job_config).result()
        logger.info("Wrote ingestion_log row: run_id=%s status=%s records=%d",
                    run_id, status, records_written)
    except Exception as exc:
        logger.error("Failed to write ingestion_log row: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    run_started_at = datetime.now(timezone.utc)
    ingested_at = run_started_at.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    run_id = f"{run_started_at.strftime('%Y%m%dT%H%M%SZ')}"

    bq_client = bigquery.Client(project=PROJECT_ID, location="EU")

    all_rows: list[dict] = []
    api_calls = 0
    api_errors = 0
    failed_station_ids: list[str] = []
    stations_polled = 0
    bq_write_error: str | None = None
    unhandled_exception: Exception | None = None

    try:
        stations = get_station_locations(bq_client)
        if not stations:
            logger.warning("No stations found — exiting")
            _write_ingestion_log(
                bq_client=bq_client,
                run_id=run_id,
                source="open-meteo",
                status="empty",
                run_started_at=run_started_at,
                run_finished_at=datetime.now(timezone.utc),
                records_written=0,
                sensors_queried=None,
                sensors_failed=None,
                stations_polled=0,
                stations_failed=0,
                api_calls=0,
                api_errors=0,
                window_start_utc=None,
                window_end_utc=None,
                error_message=None,
                failed_sensor_ids=None,
                failed_station_ids=None,
            )
            return

        stations_polled = len(stations)

        for batch_start in range(0, len(stations), BATCH_SIZE):
            batch = stations[batch_start : batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1

            station_ids = [s.station_id for s in batch]
            latitudes = [s.latitude for s in batch]
            longitudes = [s.longitude for s in batch]

            try:
                results = fetch_batch(latitudes, longitudes)
                rows = parse_batch(
                    results, station_ids, latitudes, longitudes, ingested_at
                )
                all_rows.extend(rows)
                api_calls += 1
                logger.info(
                    "Batch %d: %d stations → %d rows",
                    batch_num, len(batch), len(rows),
                )
            except requests.exceptions.HTTPError as exc:
                logger.error("Batch %d HTTP error: %s", batch_num, exc)
                api_errors += 1
                failed_station_ids.extend([str(s.station_id) for s in batch])
            except Exception as exc:
                logger.error("Batch %d failed: %s", batch_num, exc)
                api_errors += 1
                failed_station_ids.extend([str(s.station_id) for s in batch])

            time.sleep(0.5)

        # ---- Load to BigQuery ----
        if all_rows:
            try:
                load_to_bigquery(bq_client, all_rows)
            except Exception as exc:
                bq_write_error = f"{type(exc).__name__}: {exc}"
                logger.error("BigQuery write failed: %s", bq_write_error)

    except Exception as exc:
        unhandled_exception = exc
        logger.exception("Unhandled exception in weather poller: %s", exc)

    finally:
        # ---- determine status ---------------------------------------------------
        run_finished_at = datetime.now(timezone.utc)

        if unhandled_exception is not None:
            status = "error"
            error_message = f"{type(unhandled_exception).__name__}: {unhandled_exception}"
        elif bq_write_error is not None:
            status = "error"
            error_message = bq_write_error
        elif len(all_rows) == 0:
            status = "empty"
            error_message = None
        elif failed_station_ids:
            status = "partial_success"
            error_message = None
        else:
            status = "success"
            error_message = None

        # ---- write ingestion log (always, even on failure) --------------------
        _write_ingestion_log(
            bq_client=bq_client,
            run_id=run_id,
            source="open-meteo",
            status=status,
            run_started_at=run_started_at,
            run_finished_at=run_finished_at,
            records_written=len(all_rows),
            sensors_queried=None,
            sensors_failed=None,
            stations_polled=stations_polled,
            stations_failed=len(failed_station_ids),
            api_calls=api_calls,
            api_errors=api_errors,
            window_start_utc=None,
            window_end_utc=None,
            error_message=error_message,
            failed_sensor_ids=None,
            failed_station_ids=failed_station_ids if failed_station_ids else None,
        )

    duration = round((run_finished_at - run_started_at).total_seconds(), 1)
    logger.info(
        "Weather poller complete: %d rows in %.1fs (%d errors)",
        len(all_rows), duration, api_errors,
    )


if __name__ == "__main__":
    main()