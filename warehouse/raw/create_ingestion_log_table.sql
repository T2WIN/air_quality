-- ============================================================
-- Ingestion run log table for air quality pipeline
-- Append-only table that both pollers write to at the end of
-- every run, regardless of outcome.
-- ============================================================
-- Run with: bq query --use_legacy_sql=false < warehouse/raw/create_ingestion_log_table.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.${BQ_RAW_DATASET}.ingestion_log` (
  run_id STRING NOT NULL,
  source STRING NOT NULL,          -- 'openaq' or 'open-meteo'
  status STRING NOT NULL,           -- 'success', 'partial_success', 'error', 'empty'
  run_started_at TIMESTAMP NOT NULL,
  run_finished_at TIMESTAMP NOT NULL,
  duration_seconds FLOAT64,
  records_written INT64,
  sensors_queried INT64,                    -- OpenAQ-specific, nullable
  sensors_failed INT64,                    -- OpenAQ-specific, nullable
  stations_polled INT64,                    -- Weather-specific, nullable
  stations_failed INT64,                    -- Weather-specific, nullable
  api_calls INT64,
  api_errors INT64,
  window_start_utc TIMESTAMP,
  window_end_utc TIMESTAMP,
  error_message STRING,                   -- Nullable; top-level exception text
  failed_sensor_ids STRING,                   -- JSON array, nullable (OpenAQ)
  failed_station_ids STRING,                   -- JSON array, nullable (Weather)
  ingested_at TIMESTAMP NOT NULL        -- When the log row itself was written
)
PARTITION BY DATE(run_started_at)
OPTIONS (
  description = 'Append-only ingestion run log for OpenAQ and Weather pollers. Partitioned by run date.',
  require_partition_filter = FALSE
);
