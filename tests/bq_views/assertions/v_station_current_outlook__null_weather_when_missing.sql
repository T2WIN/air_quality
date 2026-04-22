-- Assert: Stations without weather data still appear with NULL weather columns
-- Station_b has no weather forecasts in fixtures
-- Also verifies the row exists and forecast_hours_count = 0

WITH expected AS (
  SELECT
    'station_b' AS station_id,
    CAST(NULL AS FLOAT64) AS avg_temperature_2m,
    CAST(NULL AS FLOAT64) AS avg_relative_humidity_2m,
    CAST(NULL AS FLOAT64) AS avg_cloud_cover,
    CAST(NULL AS FLOAT64) AS avg_boundary_layer_height,
    CAST(NULL AS FLOAT64) AS total_precipitation,
    CAST(NULL AS FLOAT64) AS max_wind_speed_10m,
    0 AS forecast_hours_count
),
actual AS (
  SELECT
    station_id,
    avg_temperature_2m,
    avg_relative_humidity_2m,
    avg_cloud_cover,
    avg_boundary_layer_height,
    total_precipitation,
    max_wind_speed_10m,
    COALESCE(forecast_hours_count, 0) AS forecast_hours_count
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_current_outlook`
  WHERE station_id = 'station_b'
)
SELECT
  'v_station_current_outlook__null_weather_when_missing' AS test_name,
  e.station_id AS entity_id,
  'all_weather_null_and_count_0' AS expected_value,
  CONCAT('temp:', CAST(a.avg_temperature_2m AS STRING),
         ',humidity:', CAST(a.avg_relative_humidity_2m AS STRING),
         ',cloud:', CAST(a.avg_cloud_cover AS STRING),
         ',boundary:', CAST(a.avg_boundary_layer_height AS STRING),
         ',precip:', CAST(a.total_precipitation AS STRING),
         ',wind:', CAST(a.max_wind_speed_10m AS STRING),
         ',count:', CAST(a.forecast_hours_count AS STRING)) AS actual_value,
  'Station without weather should have NULL weather columns and count=0' AS reason
FROM expected e
LEFT JOIN actual a ON e.station_id = a.station_id
WHERE a.station_id IS NULL  -- Row must exist
   OR a.avg_temperature_2m IS NOT NULL
   OR a.avg_relative_humidity_2m IS NOT NULL
   OR a.avg_cloud_cover IS NOT NULL
   OR a.avg_boundary_layer_height IS NOT NULL
   OR a.total_precipitation IS NOT NULL
   OR a.max_wind_speed_10m IS NOT NULL
   OR a.forecast_hours_count != 0;
