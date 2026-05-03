"""Unit tests for ingestion.static.station_metadata."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from google.cloud import bigquery

from ingestion.static.station_metadata import (
    build_metadata_rows,
    build_sensor_rows,
    filter_locations,
    write_to_bigquery,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_location(**overrides: object) -> dict:
    base = {
        "id": 42,
        "name": "Station Alpha",
        "locality": "Lyon",
        "isMobile": False,
        "isMonitor": True,
        "coordinates": {"latitude": 45.764, "longitude": 4.835},
        "country": {"code": "FR", "name": "France"},
        "timezone": "Europe/Paris",
        "provider": {"id": 1, "name": "ATMO"},
        "owner": {"id": 10, "name": "ATMO AURA"},
        "datetimeFirst": {"utc": "2020-01-01T00:00:00Z", "local": "2020-01-01T01:00:00+01:00"},
        "datetimeLast": {"utc": "2024-06-01T00:00:00Z", "local": "2024-06-01T02:00:00+02:00"},
        "sensors": [
            {
                "id": 100,
                "parameter": {"id": 1, "name": "pm25", "displayName": "PM2.5", "units": "µg/m³"},
            },
            {
                "id": 101,
                "parameter": {"id": 2, "name": "pm10", "displayName": "PM10", "units": "µg/m³"},
            },
            {
                "id": 102,
                "parameter": {"id": 3, "name": "no2", "displayName": "NO2", "units": "µg/m³"},
            },
        ],
    }
    for k, v in overrides.items():
        if v is None:
            base.pop(k, None)
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# filter_locations
# ---------------------------------------------------------------------------


class TestFilterLocations:
    def test_mobile_location_skipped(self) -> None:
        loc = _make_location(isMobile=True)
        result = filter_locations([loc])
        assert len(result) == 0

    def test_not_monitor_skipped(self) -> None:
        loc = _make_location(isMonitor=False)
        result = filter_locations([loc])
        assert len(result) == 0

    def test_missing_coordinates_skipped(self) -> None:
        loc = _make_location(coordinates={})
        result = filter_locations([loc])
        assert len(result) == 0

    def test_no_target_pollutant_skipped(self) -> None:
        loc = _make_location(sensors=[{"id": 200, "parameter": {"id": 99, "name": "co", "units": "µg/m³"}}])
        result = filter_locations([loc])
        assert len(result) == 0

    def test_valid_location_kept(self) -> None:
        loc = _make_location()
        result = filter_locations([loc])
        assert len(result) == 1
        assert result[0]["_target_pollutants"] == {"pm25", "pm10", "no2"}

    def test_mix_of_valid_and_skipped(self) -> None:
        valid = _make_location(id=1)
        mobile = _make_location(id=2, isMobile=True)
        no_pollutant = _make_location(
            id=3,
            sensors=[{"id": 300, "parameter": {"id": 99, "name": "co", "units": "µg/m³"}}],
        )
        result = filter_locations([mobile, valid, no_pollutant])
        assert len(result) == 1
        assert result[0]["id"] == 1


# ---------------------------------------------------------------------------
# build_metadata_rows
# ---------------------------------------------------------------------------


class TestBuildMetadataRows:
    def test_basic_rows(self) -> None:
        loc = _make_location()
        loc["_target_pollutants"] = {"pm25", "pm10"}
        rows = build_metadata_rows([loc])

        assert len(rows) == 1
        row = rows[0]
        assert row["station_id"] == "openaq:42"
        assert row["openaq_location_id"] == 42
        assert row["station_name"] == "Station Alpha"
        assert row["locality"] == "Lyon"
        assert row["country_code"] == "FR"
        assert row["latitude"] == 45.764
        assert row["longitude"] == 4.835
        assert row["timezone"] == "Europe/Paris"
        assert row["is_mobile"] is False
        assert row["is_monitor"] is True
        assert row["provider_name"] == "ATMO"
        assert row["pollutants_available"] == "pm10,pm25"
        assert row["sensor_count"] == 3
        assert row["loaded_at"] is not None
        raw = json.loads(row["raw_json"])
        assert raw["name"] == "Station Alpha"

    def test_missing_nested_fields(self) -> None:
        loc = _make_location(
            country=None,
            provider=None,
            owner=None,
            coordinates=None,
            datetimeFirst=None,
            datetimeLast=None,
            sensors=None,
        )
        loc["_target_pollutants"] = set()
        rows = build_metadata_rows([loc])

        assert len(rows) == 1
        row = rows[0]
        assert row["country_code"] is None
        assert row["latitude"] is None
        assert row["longitude"] is None
        assert row["provider_name"] is None
        assert row["datetime_first_utc"] is None
        assert row["sensor_count"] == 0
        assert row["pollutants_available"] == ""


# ---------------------------------------------------------------------------
# build_sensor_rows
# ---------------------------------------------------------------------------


class TestBuildSensorRows:
    def test_only_target_pollutants_included(self) -> None:
        loc = _make_location(
            sensors=[
                {"id": 100, "parameter": {"id": 1, "name": "pm25", "displayName": "PM2.5", "units": "µg/m³"}},
                {"id": 101, "parameter": {"id": 2, "name": "pm10", "displayName": "PM10", "units": "µg/m³"}},
                {"id": 102, "parameter": {"id": 99, "name": "co", "displayName": "CO", "units": "µg/m³"}},
                {"id": 103, "parameter": {"id": 3, "name": "no2", "displayName": "NO2", "units": "µg/m³"}},
            ]
        )
        rows = build_sensor_rows([loc])
        assert len(rows) == 3
        names = {r["parameter_name"] for r in rows}
        assert names == {"pm25", "pm10", "no2"}
        assert "co" not in names

    def test_basic_sensor_row_fields(self) -> None:
        loc = _make_location(
            sensors=[
                {"id": 100, "parameter": {"id": 1, "name": "pm25", "displayName": "PM2.5", "units": "µg/m³"}},
            ]
        )
        rows = build_sensor_rows([loc])
        assert len(rows) == 1
        row = rows[0]
        assert row["station_id"] == "openaq:42"
        assert row["openaq_location_id"] == 42
        assert row["openaq_sensor_id"] == 100
        assert row["parameter_id"] == 1
        assert row["parameter_name"] == "pm25"
        assert row["parameter_display_name"] == "PM2.5"
        assert row["parameter_units"] == "µg/m³"
        assert row["loaded_at"] is not None


# ---------------------------------------------------------------------------
# write_to_bigquery
# ---------------------------------------------------------------------------


class TestWriteToBigquery:
    def test_successful_write(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        bq.load_table_from_json.return_value.result.return_value = None
        bq.get_table.return_value.num_rows = 5

        rows = [{"station_id": "openaq:1"}]
        count = write_to_bigquery(bq, "project.dataset.table", rows)
        assert count == 5
        bq.load_table_from_json.assert_called_once()
        bq.get_table.assert_called_once()

    def test_write_disposition_is_truncate(self) -> None:
        bq = MagicMock(spec=bigquery.Client)
        bq.load_table_from_json.return_value.result.return_value = None
        bq.get_table.return_value.num_rows = 3

        write_to_bigquery(bq, "project.dataset.table", [{"station_id": "openaq:1"}])
        args, kwargs = bq.load_table_from_json.call_args
        assert kwargs["job_config"].write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE
