-- ============================================================
-- v_station_latest_pollutants: Latest pollutant reading per station
-- Pivots pm25, pm10, no2 into separate columns
-- ============================================================

CREATE OR REPLACE VIEW `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_latest_pollutants` AS
WITH latest_per_pollutant AS (
  -- Get the latest reading for each pollutant per station
  SELECT
    station_id,
    pollutant,
    value AS pollutant_value,
    unit AS pollutant_unit,
    period_from_utc AS reading_time,
    ROW_NUMBER() OVER (
      PARTITION BY station_id, pollutant
      ORDER BY period_from_utc DESC, ingested_at DESC
    ) AS rn
  FROM `${PROJECT_ID}.${BQ_STAGING_DATASET}.v_openaq_deduped`
  WHERE pollutant IN ('pm25', 'pm10', 'no2')
),
latest_filtered AS (
  SELECT * FROM latest_per_pollutant
WHERE rn = 1
),
pivoted AS (
  SELECT
    station_id,
    MAX(CASE WHEN pollutant = 'pm25' THEN pollutant_value END) AS pm25_value,
    MAX(CASE WHEN pollutant = 'pm25' THEN pollutant_unit END) AS pm25_unit,
    MAX(CASE WHEN pollutant = 'pm25' THEN reading_time END) AS pm25_time,
    MAX(CASE WHEN pollutant = 'pm10' THEN pollutant_value END) AS pm10_value,
    MAX(CASE WHEN pollutant = 'pm10' THEN pollutant_unit END) AS pm10_unit,
    MAX(CASE WHEN pollutant = 'pm10' THEN reading_time END) AS pm10_time,
    MAX(CASE WHEN pollutant = 'no2' THEN pollutant_value END) AS no2_value,
    MAX(CASE WHEN pollutant = 'no2' THEN pollutant_unit END) AS no2_unit,
    MAX(CASE WHEN pollutant = 'no2' THEN reading_time END) AS no2_time,
    -- Overall latest reading time for the station
    MAX(reading_time) AS latest_reading_time
  FROM latest_filtered
  GROUP BY station_id
)
SELECT
  p.*,
  m.station_name,
  m.locality,
  m.country_code,
  m.latitude,
  m.longitude
FROM pivoted AS p
LEFT JOIN `${PROJECT_ID}.${BQ_RAW_DATASET}.station_metadata` AS m
  ON p.station_id = m.station_id;
