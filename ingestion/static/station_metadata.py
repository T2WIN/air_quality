"""
Fetch French air quality monitoring stations from OpenAQ v3,
filter to relevant stations, write to BigQuery.

Run locally:
    export PROJECT_ID=your_project
    export OPENAQ_SECRET_ID=your_secret_name  # Defaults to "openaq_api_key" if not set
    python ingestion/static/station_metadata.py

This is a batch script, not a Cloud Run service.
Run it once to populate metadata, re-run monthly to refresh.
"""

import json
import os
import sys
import time
from datetime import UTC, datetime

import requests
from google.cloud import bigquery, secretmanager

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_ID = os.getenv("PROJECT_ID")
DATASET = os.getenv("BQ_DATASET", "air_quality_raw")
OPENAQ_SECRET_ID = "OPENAQ_API_KEY"

OPENAQ_BASE_URL = "https://api.openaq.org/v3"

TARGET_POLLUTANTS = {"pm25", "pm10", "o3", "no2", "so2"}

# Rhône valley bounding box for dev subset tagging
DEV_LAT_MIN, DEV_LAT_MAX = 43.5, 46.0
DEV_LON_MIN, DEV_LON_MAX = 4.0, 5.5

# Will be populated from Secret Manager at runtime
OPENAQ_API_KEY = None


# ---------------------------------------------------------------------------
# Secret Manager
# ---------------------------------------------------------------------------


def get_secret(project_id: str, secret_id: str, version_id: str = "latest") -> str:
    """
    Fetch a secret payload from Google Cloud Secret Manager.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


# ---------------------------------------------------------------------------
# OpenAQ API
# ---------------------------------------------------------------------------


def fetch_french_locations() -> list[dict]:
    """
    Paginate through GET /v3/locations to get all French stations.
    Returns the raw list of location dicts from the API.
    """
    headers = {"X-API-Key": OPENAQ_API_KEY}
    locations = []
    page = 1

    print("Fetching French locations from OpenAQ...")

    while True:
        params = {
            "iso": "FR",
            "limit": 200,
            "page": page,
        }

        resp = requests.get(
            f"{OPENAQ_BASE_URL}/locations",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()

        data = resp.json()
        results = data.get("results", [])

        if not results:
            break

        locations.extend(results)
        found = data.get("meta", {}).get("found", "?")
        print(
            f"  Page {page}: {len(results)} locations (running total: {len(locations)}, API says found: {found})"
        )

        # If found is a definite number and we have them all, stop
        if isinstance(found, int) and len(locations) >= found:
            break

        page += 1
        time.sleep(0.3)

    print(f"  Total raw locations fetched: {len(locations)}")
    return locations


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def filter_locations(locations: list[dict]) -> list[dict]:
    """
    Keep only stationary reference monitors that measure
    at least one of our target pollutants.
    """
    filtered = []
    skipped = {"mobile": 0, "not_monitor": 0, "no_coords": 0, "no_target_pollutant": 0}

    for loc in locations:
        if loc.get("isMobile", True):
            skipped["mobile"] += 1
            continue

        if not loc.get("isMonitor", False):
            skipped["not_monitor"] += 1
            continue

        coords = loc.get("coordinates") or {}
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        if lat is None or lon is None:
            skipped["no_coords"] += 1
            continue

        # Find which target pollutants this station has
        sensors = loc.get("sensors") or []
        station_pollutants = set()
        for sensor in sensors:
            param = sensor.get("parameter") or {}
            name = param.get("name", "")
            if name in TARGET_POLLUTANTS:
                station_pollutants.add(name)

        if not station_pollutants:
            skipped["no_target_pollutant"] += 1
            continue

        # Attach for downstream use
        loc["_target_pollutants"] = station_pollutants
        filtered.append(loc)

    print("\nFiltering results:")
    print(f"  Kept: {len(filtered)}")
    for reason, count in skipped.items():
        if count > 0:
            print(f"  Skipped ({reason}): {count}")

    return filtered


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def build_metadata_rows(locations: list[dict]) -> list[dict]:
    """Build rows for the station_metadata table."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []

    for loc in locations:
        loc_id = loc["id"]
        coords = loc.get("coordinates") or {}
        country = loc.get("country") or {}
        provider = loc.get("provider") or {}
        owner = loc.get("owner") or {}
        dt_first = loc.get("datetimeFirst") or {}
        dt_last = loc.get("datetimeLast") or {}
        target_pollutants = loc.get("_target_pollutants", set())

        row = {
            "station_id": f"openaq:{loc_id}",
            "openaq_location_id": loc_id,
            "station_name": loc.get("name"),
            "locality": loc.get("locality"),
            "country_code": country.get("code"),
            "country_name": country.get("name"),
            "latitude": coords.get("latitude"),
            "longitude": coords.get("longitude"),
            "timezone": loc.get("timezone"),
            "is_mobile": loc.get("isMobile", False),
            "is_monitor": loc.get("isMonitor", True),
            "provider_id": provider.get("id"),
            "provider_name": provider.get("name"),
            "owner_id": owner.get("id"),
            "owner_name": owner.get("name"),
            "datetime_first_utc": dt_first.get("utc"),
            "datetime_last_utc": dt_last.get("utc"),
            "pollutants_available": ",".join(sorted(target_pollutants)),
            "sensor_count": len(loc.get("sensors") or []),
            "raw_json": json.dumps(loc, default=str),
            "loaded_at": now,
        }
        rows.append(row)

    return rows


def build_sensor_rows(locations: list[dict]) -> list[dict]:
    """
    Build rows for the station_sensors table.
    Only includes sensors for target pollutants.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []

    for loc in locations:
        loc_id = loc["id"]
        station_id = f"openaq:{loc_id}"

        for sensor in loc.get("sensors") or []:
            param = sensor.get("parameter") or {}
            param_name = param.get("name", "")

            if param_name not in TARGET_POLLUTANTS:
                continue

            row = {
                "station_id": station_id,
                "openaq_location_id": loc_id,
                "openaq_sensor_id": sensor["id"],
                "parameter_id": param.get("id"),
                "parameter_name": param_name,
                "parameter_display_name": param.get("displayName"),
                "parameter_units": param.get("units"),
                "loaded_at": now,
            }
            rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# BigQuery writes
# ---------------------------------------------------------------------------


def write_to_bigquery(
    client: bigquery.Client,
    table_id: str,
    rows: list[dict],
) -> int:
    """
    Full-refresh write: truncate and reload.
    Returns number of rows written.
    """
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )

    job = client.load_table_from_json(rows, table_id, job_config=job_config)
    job.result()  # blocks until done

    table = client.get_table(table_id)
    return table.num_rows


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(metadata_rows: list[dict], sensor_rows: list[dict]):
    """Print a human-readable summary of what was loaded."""

    print(f"\n{'=' * 60}")
    print("STATION METADATA SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total stations: {len(metadata_rows)}")

    # Per-pollutant counts
    pollutant_counts = dict.fromkeys(sorted(TARGET_POLLUTANTS), 0)
    for row in metadata_rows:
        for p in row["pollutants_available"].split(","):
            if p in pollutant_counts:
                pollutant_counts[p] += 1

    print("\nStations per pollutant:")
    for p, count in pollutant_counts.items():
        print(f"  {p:>6s}: {count}")

    # Coverage tiers
    has_all_5 = sum(1 for r in metadata_rows if len(r["pollutants_available"].split(",")) == 5)
    has_4_plus = sum(1 for r in metadata_rows if len(r["pollutants_available"].split(",")) >= 4)
    print(f"\nStations with all 5 pollutants: {has_all_5}")
    print(f"Stations with 4+ pollutants:    {has_4_plus}")

    # Dev subset (Rhône valley)
    dev_stations = [
        r
        for r in metadata_rows
        if (
            r["latitude"] is not None
            and DEV_LAT_MIN <= r["latitude"] <= DEV_LAT_MAX
            and DEV_LON_MIN <= r["longitude"] <= DEV_LON_MAX
        )
    ]
    print(
        f"\nRhône valley dev subset ({DEV_LAT_MIN}-{DEV_LAT_MAX}N, {DEV_LON_MIN}-{DEV_LON_MAX}E):"
    )
    print(f"  Stations: {len(dev_stations)}")
    for s in dev_stations:
        print(
            f"    {s['station_id']:>20s}  {s['station_name']:<30s}  [{s['pollutants_available']}]"
        )

    # Sensors
    print(f"\nTotal sensor rows: {len(sensor_rows)}")

    # Unit check
    units_by_pollutant = {}
    for row in sensor_rows:
        p = row["parameter_name"]
        u = row["parameter_units"]
        units_by_pollutant.setdefault(p, set()).add(u)

    print("\nUnits by pollutant (watch for non-µg/m³):")
    for p in sorted(units_by_pollutant):
        units = units_by_pollutant[p]
        flag = "" if units == {"µg/m³"} else "  ⚠️  NEEDS CONVERSION"
        print(f"  {p:>6s}: {units}{flag}")

    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    global OPENAQ_API_KEY

    # Validate config
    if not PROJECT_ID:
        print("ERROR: PROJECT_ID not set")
        sys.exit(1)

    # Fetch API Key from Secret Manager
    try:
        print(f"Fetching OpenAQ API key from Secret Manager (secret: {OPENAQ_SECRET_ID})...")
        OPENAQ_API_KEY = get_secret(PROJECT_ID, OPENAQ_SECRET_ID)
    except Exception as e:
        print(f"ERROR: Failed to load secret from Secret Manager: {e}")
        sys.exit(1)

    if not OPENAQ_API_KEY:
        print("ERROR: Fetched OPENAQ_API_KEY is empty")
        sys.exit(1)

    metadata_table = f"{PROJECT_ID}.{DATASET}.station_metadata"
    sensors_table = f"{PROJECT_ID}.{DATASET}.station_sensors"

    # Fetch
    raw_locations = fetch_french_locations()

    if not raw_locations:
        print("ERROR: No locations returned from OpenAQ. Check API key and connectivity.")
        sys.exit(1)

    # Filter
    filtered = filter_locations(raw_locations)

    if not filtered:
        print("ERROR: No stations passed filters. This is unexpected for France.")
        sys.exit(1)

    # Build rows
    metadata_rows = build_metadata_rows(filtered)
    sensor_rows = build_sensor_rows(filtered)

    # Print summary before writing (so you can inspect before committing)
    print_summary(metadata_rows, sensor_rows)

    # Write to BigQuery
    print("\nWriting to BigQuery...")
    client = bigquery.Client(project=PROJECT_ID)

    n_metadata = write_to_bigquery(client, metadata_table, metadata_rows)
    print(f"  station_metadata: {n_metadata} rows written")

    n_sensors = write_to_bigquery(client, sensors_table, sensor_rows)
    print(f"  station_sensors:  {n_sensors} rows written")

    print("\nDone.")


if __name__ == "__main__":
    main()
