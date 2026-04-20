-- ============================================================
-- Raw schema for test fixtures
-- Creates tables matching production schema without partitioning
-- ============================================================

-- Station metadata: one row per monitoring station
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${BQ_RAW_DATASET}.station_metadata` (
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
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${BQ_RAW_DATASET}.station_sensors` (
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
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${BQ_RAW_DATASET}.openaq_hourly` (
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
);

-- Weather forecasts from Open-Meteo
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${BQ_RAW_DATASET}.weather_forecasts` (
  station_id              STRING NOT NULL,
  run_id                  STRING NOT NULL,
  latitude                FLOAT64 NOT NULL,
  longitude               FLOAT64 NOT NULL,
  valid_time              TIMESTAMP NOT NULL,
  temperature_2m          FLOAT64,
  relative_humidity_2m    FLOAT64,
  surface_pressure        FLOAT64,
  wind_speed_10m          FLOAT64,
  wind_direction_10m      FLOAT64,
  precipitation           FLOAT64,
  cloud_cover             FLOAT64,
  boundary_layer_height   FLOAT64,
  ingested_at             TIMESTAMP NOT NULL,
  dedup_key               STRING NOT NULL
);

-- Ingestion run log for both pollers
CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${BQ_RAW_DATASET}.ingestion_log` (
  run_id                  STRING NOT NULL,
  source                  STRING NOT NULL,
  status                  STRING NOT NULL,
  run_started_at          TIMESTAMP NOT NULL,
  run_finished_at         TIMESTAMP NOT NULL,
  duration_seconds        FLOAT64,
  records_written         INT64,
  sensors_queried         INT64,
  sensors_failed          INT64,
  stations_polled         INT64,
  stations_failed         INT64,
  api_calls               INT64,
  api_errors              INT64,
  window_start_utc        TIMESTAMP,
  window_end_utc          TIMESTAMP,
  error_message           STRING,
  failed_sensor_ids       STRING,
  failed_station_ids      STRING,
  ingested_at             TIMESTAMP NOT NULL
);
