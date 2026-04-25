-- Assert: Station A at 07:00 UTC has pm25 but NULL weather columns
-- Tests that LEFT JOIN produces NULL weather when no matching hour exists

WITH expected AS (
  SELECT
    'station_a' AS station_id,
    TIMESTAMP '2026-03-22 07:00:00 UTC' AS hour_utc,
    50.0 AS expected_pm25
),
actual AS (
  SELECT
    station_id,
    hour_utc,
    pm25_value,
    temperature_2m,
    wind_speed_10m,
    boundary_layer_height
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_hourly_combined`
  WHERE station_id = 'station_a' AND hour_utc = TIMESTAMP '2026-03-22 07:00:00 UTC'
)
SELECT
  'v_station_hourly_combined__null_weather_hours_exist' AS test_name,
  e.station_id AS entity_id,
  'pm25:50.0, weather: NULL' AS expected_value,
  CONCAT('pm25:', CAST(a.pm25_value AS STRING),
         ',temp:', CAST(a.temperature_2m AS STRING),
         ',wind:', CAST(a.wind_speed_10m AS STRING),
         ',blh:', CAST(a.boundary_layer_height AS STRING)) AS actual_value,
  'Station A 07:00 should have pm25=50.0 but NULL weather columns' AS reason
FROM expected e
LEFT JOIN actual a ON e.station_id = a.station_id AND e.hour_utc = a.hour_utc
WHERE a.station_id IS NULL
   OR ABS(COALESCE(a.pm25_value, -1) - e.expected_pm25) > 0.01
   OR a.temperature_2m IS NOT NULL
   OR a.wind_speed_10m IS NOT NULL
   OR a.boundary_layer_height IS NOT NULL;