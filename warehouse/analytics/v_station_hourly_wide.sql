-- ============================================================
-- v_station_hourly_wide: Hourly air quality data pivoted wide
-- One row per station per hour with pollutant columns
-- ============================================================

CREATE OR REPLACE VIEW `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_hourly_wide` AS
WITH hourly_pollutants AS (
  SELECT
    station_id,
    period_from_utc AS hour_utc,
    pollutant,
    value,
    unit,
    coverage_pct,
    ingested_at
  FROM `${PROJECT_ID}.${BQ_STAGING_DATASET}.v_openaq_deduped`
  WHERE pollutant IN ('pm25', 'pm10', 'no2')
)
SELECT
  station_id,
  hour_utc,
  MAX(CASE WHEN pollutant = 'pm25' THEN value END) AS pm25_value,
  MAX(CASE WHEN pollutant = 'pm25' THEN unit END) AS pm25_unit,
  MAX(CASE WHEN pollutant = 'pm10' THEN value END) AS pm10_value,
  MAX(CASE WHEN pollutant = 'pm10' THEN unit END) AS pm10_unit,
  MAX(CASE WHEN pollutant = 'no2' THEN value END) AS no2_value,
  MAX(CASE WHEN pollutant = 'no2' THEN unit END) AS no2_unit,
  -- Metadata from most recent ingested row for this hour
  MAX(ingested_at) AS latest_ingested_at
FROM hourly_pollutants
GROUP BY station_id, hour_utc;
