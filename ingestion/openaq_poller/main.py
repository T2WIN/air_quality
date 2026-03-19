import json
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, jsonify
from google.cloud import bigquery, pubsub_v1
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

app = Flask(__name__)

TARGET_POLLUTANTS = {"pm25", "pm10", "o3", "no2", "so2"}
_thread_local = threading.local()

bq_client = bigquery.Client()
publisher_client = pubsub_v1.PublisherClient()


@dataclass(frozen=True)
class Config:
    project_id: str
    raw_dataset: str
    station_sensors_table: str
    hourly_table: str
    pubsub_topic: Optional[str]
    openaq_base_url: str
    openaq_api_key: Optional[str]
    lookback_hours: int
    max_workers: int
    http_timeout_seconds: int
    dev_station_ids: List[str]
    max_sensors: Optional[int]
    enforce_complete_hours: bool

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
            raw_dataset=os.getenv("BQ_DATASET", "air_quality_raw"),
            station_sensors_table=os.getenv("BQ_STATION_SENSORS_TABLE", "station_sensors"),
            hourly_table=os.getenv("BQ_OPENAQ_HOURLY_TABLE", "openaq_hourly"),
            pubsub_topic=(os.getenv("PUBSUB_TOPIC", "raw-data-ingested") or "").strip() or None,
            openaq_base_url=os.getenv("OPENAQ_BASE_URL", "https://api.openaq.org/v3").rstrip("/"),
            openaq_api_key=(os.getenv("OPENAQ_API_KEY", "") or "").strip() or None,
            lookback_hours=int(os.getenv("LOOKBACK_HOURS", "3")),
            max_workers=int(os.getenv("MAX_WORKERS", "8")),
            http_timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            dev_station_ids=parse_csv_env("DEV_STATION_IDS"),
            max_sensors=parse_optional_int(os.getenv("MAX_SENSORS")),
            enforce_complete_hours=parse_bool_env("ENFORCE_COMPLETE_HOURS", default=True),
        )


def parse_csv_env(name: str) -> List[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "t", "yes", "y"}


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


def get_query_window(now: datetime, lookback_hours: int) -> tuple[datetime, datetime]:
    """
    Query the last N complete clock hours.
    Example at 10:05Z with lookback=3:
      start = 07:00Z
      end   = 10:00Z
    """
    window_end = now.replace(minute=0, second=0, microsecond=0)
    window_start = window_end - timedelta(hours=lookback_hours)
    logging.info((window_start, window_end))
    return window_start, window_end


def get_requests_session(config: Config) -> requests.Session:
    if not hasattr(_thread_local, "session"):
        session = requests.Session()

        retry = Retry(
            total=4,
            connect=4,
            read=4,
            status=4,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=config.max_workers,
            pool_maxsize=config.max_workers,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        headers = {"Accept": "application/json"}
        if config.openaq_api_key:
            headers["X-API-Key"] = config.openaq_api_key

        session.headers.update(headers)
        _thread_local.session = session

    return _thread_local.session


def load_station_sensors(config: Config) -> List[Dict[str, Any]]:
    sql = f"""
        SELECT
          station_id,
          openaq_location_id,
          openaq_sensor_id,
          parameter_name AS pollutant,
          parameter_units AS unit
        FROM `{config.project_id}.{config.raw_dataset}.{config.station_sensors_table}`
        WHERE parameter_name IN UNNEST(@pollutants)
    """

    query_parameters = [
        bigquery.ArrayQueryParameter(
            "pollutants", "STRING", sorted(TARGET_POLLUTANTS)
        )
    ]

    if config.dev_station_ids:
        sql += "\nAND station_id IN UNNEST(@station_ids)"
        query_parameters.append(
            bigquery.ArrayQueryParameter(
                "station_ids", "STRING", config.dev_station_ids
            )
        )

    sql += "\nORDER BY station_id, pollutant, openaq_sensor_id"

    job_config = bigquery.QueryJobConfig(query_parameters=query_parameters)
    rows = [dict(row) for row in bq_client.query(sql, job_config=job_config).result()]

    if config.max_sensors is not None:
        rows = rows[: config.max_sensors]

    logging.info("Loaded %s sensors from BigQuery lookup", len(rows))
    return rows


def fetch_sensor_hours(
    config: Config,
    sensor: Dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    ingested_at: datetime,
    run_id: str,
) -> List[Dict[str, Any]]:
    session = get_requests_session(config)
    sensor_id = sensor["openaq_sensor_id"]

    url = f"{config.openaq_base_url}/sensors/{sensor_id}/hours"
    params = {
        "datetime_from": to_rfc3339_z(window_start),
        "datetime_to": to_rfc3339_z(window_end),
        "limit": 24,  # plenty for a 3-hour lookback
        "page": 1,
    }
    logging.info(f"TEST PARAMS : {params}")
    response = session.get(url, params=params, timeout=config.http_timeout_seconds)
    response.raise_for_status()

    payload = response.json()
    results = payload.get("results", [])
    transformed_rows = []

    for item in results:
        row = transform_hour_row(
            sensor=sensor,
            hour_payload=item,
            ingested_at=ingested_at,
            run_id=run_id,
            window_end=window_end,
            enforce_complete_hours=config.enforce_complete_hours,
        )
        if row is not None:
            transformed_rows.append(row)

    return transformed_rows


def transform_hour_row(
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
        # Avoid current incomplete hour if API returns it.
        return None

    pollutant = sensor["pollutant"]
    station_id = sensor["station_id"]

    return {
        "ingested_at": to_rfc3339_z(ingested_at),
        "run_id": run_id,
        "station_id": station_id,
        "openaq_location_id": sensor["openaq_location_id"],
        "openaq_sensor_id": sensor["openaq_sensor_id"],
        "pollutant": pollutant,
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
        "dedup_key": f"{station_id}|{pollutant}|{period_from_utc}",
    }


def append_rows_to_bigquery(config: Config, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        logging.info("No rows to write to BigQuery")
        return

    table_id = f"{config.project_id}.{config.raw_dataset}.{config.hourly_table}"
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )

    load_job = bq_client.load_table_from_json(
        rows,
        table_id,
        job_config=job_config,
    )
    load_job.result()

    logging.info("Appended %s rows to %s", len(rows), table_id)


def publish_summary_event(config: Config, summary: Dict[str, Any]) -> None:
    if not config.pubsub_topic:
        logging.info("PUBSUB_TOPIC not set; skipping Pub/Sub publish")
        return

    topic_path = publisher_client.topic_path(config.project_id, config.pubsub_topic)
    data = json.dumps(summary, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    future = publisher_client.publish(topic_path, data)
    future.result(timeout=30)

    logging.info("Published summary event to %s", topic_path)


def run_poller(config: Config) -> Dict[str, Any]:
    run_started_at = utc_now()
    ingested_at = run_started_at
    run_id = build_run_id(run_started_at)
    window_start, window_end = get_query_window(run_started_at, config.lookback_hours)

    logging.info(
        "Starting OpenAQ poller run_id=%s window=[%s, %s)",
        run_id,
        to_rfc3339_z(window_start),
        to_rfc3339_z(window_end),
    )

    sensors = load_station_sensors(config)
    sensors_queried = len(sensors)

    if sensors_queried == 0:
        summary = {
            "source": "openaq",
            "run_id": run_id,
            "ingestion_timestamp": to_rfc3339_z(ingested_at),
            "record_count": 0,
            "station_count": 0,
            "sensors_queried": 0,
            "sensors_with_data": 0,
            "data_timestamp_min": None,
            "data_timestamp_max": None,
            "bq_table": f"{config.raw_dataset}.{config.hourly_table}",
            "status": "success",
            "failed_sensor_count": 0,
            "window_start_utc": to_rfc3339_z(window_start),
            "window_end_utc": to_rfc3339_z(window_end),
        }
        publish_summary_event(config, summary)
        return summary

    all_rows: List[Dict[str, Any]] = []
    sensors_with_data = set()
    stations_with_data = set()
    failed_sensor_ids = []

    worker_count = min(config.max_workers, max(1, sensors_queried))
    logging.info("Querying %s sensors with %s workers", sensors_queried, worker_count)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_sensor = {
            executor.submit(
                fetch_sensor_hours,
                config,
                sensor,
                window_start,
                window_end,
                ingested_at,
                run_id,
            ): sensor
            for sensor in sensors
        }

        for future in as_completed(future_to_sensor):
            sensor = future_to_sensor[future]
            sensor_id = sensor["openaq_sensor_id"]

            try:
                rows = future.result()
            except Exception as exc:
                failed_sensor_ids.append(sensor_id)
                logging.exception(
                    "Failed sensor_id=%s station_id=%s pollutant=%s error=%s",
                    sensor_id,
                    sensor["station_id"],
                    sensor["pollutant"],
                    exc,
                )
                continue

            if rows:
                all_rows.extend(rows)
                sensors_with_data.add(sensor_id)
                for row in rows:
                    stations_with_data.add(row["station_id"])
    logging.info(all_rows[:-4])
    append_rows_to_bigquery(config, all_rows)

    timestamps = [row["period_from_utc"] for row in all_rows if row.get("period_from_utc")]
    data_timestamp_min = min(timestamps) if timestamps else None
    data_timestamp_max = max(timestamps) if timestamps else None

    status = "partial_success" if failed_sensor_ids else "success"

    summary = {
        "source": "openaq",
        "run_id": run_id,
        "ingestion_timestamp": to_rfc3339_z(ingested_at),
        "record_count": len(all_rows),
        "station_count": len(stations_with_data),
        "sensors_queried": sensors_queried,
        "sensors_with_data": len(sensors_with_data),
        "data_timestamp_min": data_timestamp_min,
        "data_timestamp_max": data_timestamp_max,
        "bq_table": f"{config.raw_dataset}.{config.hourly_table}",
        "status": status,
        # Optional diagnostics
        "failed_sensor_count": len(failed_sensor_ids),
        "window_start_utc": to_rfc3339_z(window_start),
        "window_end_utc": to_rfc3339_z(window_end),
    }
    

    publish_summary_event(config, summary)
    logging.info("Completed run summary=%s", summary)
    return summary


@app.route("/", methods=["GET", "POST"])
@app.route("/run", methods=["GET", "POST"])
def trigger() -> tuple[Any, int]:
    try:
        config = Config.from_env()
        summary = run_poller(config)
        return jsonify(summary), 200
    except Exception as exc:
        logging.exception("OpenAQ poller failed: %s", exc)
        return jsonify({"source": "openaq", "status": "error", "error": str(exc)}), 500


@app.route("/health", methods=["GET"])
def healthz() -> tuple[Any, int]:
    return jsonify({"ok": True}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)