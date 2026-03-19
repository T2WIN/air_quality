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
from google.cloud import bigquery, pubsub_v1

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ID = os.environ["PROJECT_ID"]
BQ_DATASET = os.environ.get("BQ_RAW_DATASET", "air_quality_raw")
BQ_TABLE = os.environ.get("BQ_WEATHER_TABLE", "weather_forecasts")
PUBSUB_TOPIC = os.environ.get("PUBSUB_TOPIC", "raw-data-ingested")
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
# Pub/Sub summary
# ---------------------------------------------------------------------------

def publish_summary(summary: dict) -> None:
    """Publish run summary to Pub/Sub. Best-effort — logged but not fatal."""
    try:
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(PROJECT_ID, PUBSUB_TOPIC)
        future = publisher.publish(
            topic_path, json.dumps(summary).encode("utf-8")
        )
        future.result(timeout=10)
        logger.info("Published summary to %s", PUBSUB_TOPIC)
    except Exception as exc:
        logger.error("Pub/Sub publish failed (non-fatal): %s", exc)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    run_start = time.time()
    ingested_at = (
        datetime.now(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S+00:00")
    )
    bq_client = bigquery.Client(project=PROJECT_ID, location="EU")

    stations = get_station_locations(bq_client)
    if not stations:
        logger.warning("No stations found — exiting")
        return

    all_rows: list[dict] = []
    api_calls = 0
    api_errors = 0

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
        except Exception as exc:
            logger.error("Batch %d failed: %s", batch_num, exc)
            api_errors += 1

        time.sleep(0.5)

    # ---- Load to BigQuery ----
    rows_loaded = 0
    if all_rows:
        rows_loaded = load_to_bigquery(bq_client, all_rows)
    else:
        logger.warning("No rows to load")

    # ---- Summary ----
    duration = round(time.time() - run_start, 1)
    summary = {
        "source": "open-meteo",
        "stations_polled": len(stations),
        "api_calls": api_calls,
        "api_errors": api_errors,
        "rows_loaded": rows_loaded,
        "forecast_hours": FORECAST_HOURS,
        "ingested_at": ingested_at,
        "duration_seconds": duration,
    }
    publish_summary(summary)
    logger.info(
        "Weather poller complete: %d rows in %.1fs (%d errors)",
        rows_loaded, duration, api_errors,
    )


if __name__ == "__main__":
    main()