-- ============================================================
-- Weather forecast staging views
-- Uses current raw schema in air_quality_raw.weather_forecasts
-- ============================================================

-- 1) Exact snapshot dedup
-- Keeps only one row per exact forecast snapshot.
-- If the same run is accidentally loaded twice, this removes duplicates.
CREATE OR REPLACE VIEW air_quality_staging.weather_snapshot_dedup AS
WITH ranked AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY station_id, valid_time, forecast_time
      ORDER BY ingested_at DESC, dedup_key DESC
    ) AS rn
  FROM `air_quality_raw.weather_forecasts`
)
SELECT * EXCEPT (rn)
FROM ranked
WHERE rn = 1;


-- 2) Latest forecast per station/hour
-- For each station and valid hour, keep the newest forecast snapshot.
CREATE OR REPLACE VIEW air_quality_staging.weather_latest AS
WITH ranked AS (
  SELECT
    *,
    TIMESTAMP_DIFF(valid_time, forecast_time, HOUR) AS forecast_horizon_hours,
    ROW_NUMBER() OVER (
      PARTITION BY station_id, valid_time
      ORDER BY forecast_time DESC, ingested_at DESC
    ) AS rn
  FROM `air_quality_staging.weather_snapshot_dedup`
)
SELECT * EXCEPT (rn)
FROM ranked
WHERE rn = 1;


-- 3) Convenience view for live/dashboard use
-- Restrict to recent and near-future hours to keep queries light.
CREATE OR REPLACE VIEW air_quality_staging.weather_latest_next_72h AS
SELECT *
FROM `air_quality_staging.weather_latest`
WHERE valid_time >= TIMESTAMP_SUB(TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR), INTERVAL 6 HOUR)
  AND valid_time < TIMESTAMP_ADD(TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), HOUR), INTERVAL 72 HOUR);