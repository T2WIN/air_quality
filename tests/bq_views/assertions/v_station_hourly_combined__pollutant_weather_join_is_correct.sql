-- Assert: Station A at 10:00 UTC has correct pollutant and weather values
-- Tests that LEFT JOIN correctly combines pollutant and weather at matching hour

WITH expected AS (
  SELECT
    'station_a' AS station_id,
    TIMESTAMP '2026-03-22 10:00:00 UTC' AS hour_utc,
    22.0 AS expected_pm25,
    30.0 AS expected_pm10,
    45.0 AS expected_no2,
    9.0 AS expected_temperature_2m,
    4.5 AS expected_wind_speed_10m,
    550.0 AS expected_boundary_layer_height
),
actual AS (
  SELECT
    station_id,
    hour_utc,
    pm25_value,
    pm10_value,
    no2_value,
    temperature_2m,
    wind_speed_10m,
    boundary_layer_height
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_hourly_combined`
  WHERE station_id = 'station_a' AND hour_utc = TIMESTAMP '2026-03-22 10:00:00 UTC'
)
SELECT
  'v_station_hourly_combined__pollutant_weather_join_is_correct' AS test_name,
  e.station_id AS entity_id,
  CONCAT('pm25:', CAST(e.expected_pm25 AS STRING),
         ',pm10:', CAST(e.expected_pm10 AS STRING),
         ',no2:', CAST(e.expected_no2 AS STRING),
         ',temp:', CAST(e.expected_temperature_2m AS STRING),
         ',wind:', CAST(e.expected_wind_speed_10m AS STRING),
         ',blh:', CAST(e.expected_boundary_layer_height AS STRING)) AS expected_value,
  CONCAT('pm25:', CAST(a.pm25_value AS STRING),
         ',pm10:', CAST(a.pm10_value AS STRING),
         ',no2:', CAST(a.no2_value AS STRING),
         ',temp:', CAST(a.temperature_2m AS STRING),
         ',wind:', CAST(a.wind_speed_10m AS STRING),
         ',blh:', CAST(a.boundary_layer_height AS STRING)) AS actual_value,
  'Pollutant-weather join does not match expected values' AS reason
FROM expected e
LEFT JOIN actual a ON e.station_id = a.station_id AND e.hour_utc = a.hour_utc
WHERE a.station_id IS NULL
   OR ABS(COALESCE(a.pm25_value, -1) - e.expected_pm25) > 0.01
   OR ABS(COALESCE(a.pm10_value, -1) - e.expected_pm10) > 0.01
   OR ABS(COALESCE(a.no2_value, -1) - e.expected_no2) > 0.01
   OR ABS(COALESCE(a.temperature_2m, -999) - e.expected_temperature_2m) > 0.01
   OR ABS(COALESCE(a.wind_speed_10m, -999) - e.expected_wind_speed_10m) > 0.01
   OR ABS(COALESCE(a.boundary_layer_height, -999) - e.expected_boundary_layer_height) > 0.01;