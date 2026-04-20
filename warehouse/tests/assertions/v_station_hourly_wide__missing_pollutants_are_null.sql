-- Assert: Missing pollutant columns are NULL, not duplicated or incorrectly filled
-- Station B at 08:00 has only pm25, so pm10 and no2 should be NULL

WITH station_b_08 AS (
  SELECT
    station_id,
    hour_utc,
    pm25_value,
    pm10_value,
    no2_value
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_hourly_wide`
  WHERE station_id = 'station_b'
    AND hour_utc = TIMESTAMP '2026-03-22 08:00:00 UTC'
)
SELECT
  'v_station_hourly_wide__missing_pollutants_are_null' AS test_name,
  CONCAT(station_id, ':', CAST(hour_utc AS STRING)) AS entity_id,
  'pm10=NULL,no2=NULL' AS expected_value,
  CONCAT('pm10:', COALESCE(CAST(pm10_value AS STRING), 'NULL'), ',no2:', COALESCE(CAST(no2_value AS STRING), 'NULL')) AS actual_value,
  'Missing pollutants should be NULL, not filled with other values' AS reason
FROM station_b_08
WHERE pm10_value IS NOT NULL OR no2_value IS NOT NULL;
