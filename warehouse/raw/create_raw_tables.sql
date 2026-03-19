-- ============================================================
-- Raw layer tables for air quality ingestion
-- Run with: bq query --use_legacy_sql=false < warehouse/raw/create_raw_tables.sql
-- ============================================================

-- Station metadata: one row per monitoring station
-- Small table, no partitioning needed
CREATE TABLE IF NOT EXISTS air_quality_raw.station_metadata (
  station_id              STRING NOT NULL,
  openaq_location_id      INT64 NOT NULL,
  station_name            STRING,
  locality                STRING,
  country_code            STRING,
  country_name            STRING,
  latitude                FLOAT64,
  longitude               FLOAT64,
  timezone                STRING,
  is_mobile               BOOLEAN,
  is_monitor              BOOLEAN,
  provider_id             INT64,
  provider_name           STRING,
  owner_id                INT64,
  owner_name              STRING,
  datetime_first_utc      TIMESTAMP,
  datetime_last_utc       TIMESTAMP,
  pollutants_available    STRING,
  sensor_count            INT64,
  raw_json                STRING,
  loaded_at               TIMESTAMP
);

-- Sensor lookup: one row per sensor per station
-- The poller reads this to know which sensor IDs to query
CREATE TABLE IF NOT EXISTS air_quality_raw.station_sensors (
  station_id              STRING NOT NULL,
  openaq_location_id      INT64 NOT NULL,
  openaq_sensor_id        INT64 NOT NULL,
  parameter_id            INT64,
  parameter_name          STRING,
  parameter_display_name  STRING,
  parameter_units         STRING,
  loaded_at               TIMESTAMP
);

-- Hourly pollutant measurements from OpenAQ
-- One row per station per pollutant per hour
-- ----- Hourly pollutant measurements (30-day expiration) -----
CREATE TABLE IF NOT EXISTS air_quality_raw.openaq_hourly (
  ingested_at             TIMESTAMP NOT NULL,
  run_id                  STRING NOT NULL,
  station_id              STRING NOT NULL,
  openaq_location_id      INT64 NOT NULL,
  openaq_sensor_id        INT64 NOT NULL,
  pollutant               STRING NOT NULL,
  value                   FLOAT64,
  unit                    STRING,
  period_from_utc         TIMESTAMP,
  period_to_utc           TIMESTAMP,
  period_from_local       STRING,
  period_label            STRING,
  period_interval         STRING,
  coverage_expected       INT64,
  coverage_observed       INT64,
  coverage_pct            FLOAT64,
  dedup_key               STRING NOT NULL
)
PARTITION BY DATE(period_from_utc)
CLUSTER BY station_id, pollutant
OPTIONS (
  description = 'Append-only hourly pollutant observations from OpenAQ poller. 30-day rolling retention.',
  partition_expiration_days = 30
);

-- ----- Weather forecasts (30-day expiration) -----
CREATE TABLE IF NOT EXISTS air_quality_raw.weather_forecasts (
  
  station_id          STRING      NOT NULL,
  latitude            FLOAT64     NOT NULL,
  longitude           FLOAT64     NOT NULL,
  forecast_time       TIMESTAMP   NOT NULL,
  valid_time          TIMESTAMP   NOT NULL,
  temperature_2m      FLOAT64,
  relative_humidity_2m FLOAT64,
  surface_pressure    FLOAT64,
  wind_speed_10m      FLOAT64,
  wind_direction_10m  FLOAT64,
  precipitation       FLOAT64,
  cloud_cover         FLOAT64,
  boundary_layer_height FLOAT64,
  ingested_at         TIMESTAMP   NOT NULL,
  dedup_key           STRING      NOT NULL
)
PARTITION BY DATE(valid_time)
CLUSTER BY station_id
OPTIONS (
  description = 'Hourly weather forecasts from Open-Meteo poller. 30-day rolling retention.',
  partition_expiration_days = 30
);