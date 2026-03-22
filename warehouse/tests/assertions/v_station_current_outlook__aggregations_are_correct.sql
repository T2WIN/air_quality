-- Assert: Weather aggregations are correct for station_a
-- With temps 10, 12, 14 -> avg = 12
-- With humidity 65, 60, 55 -> avg = 60
-- With cloud 20, 30, 40 -> avg = 30
-- With boundary layer 500, 600, 700 -> avg = 600
-- With precip 1, 2, 3 -> total = 6
-- With wind 5, 7, 6 -> max = 7

WITH expected AS (
  SELECT
    'station_a' AS station_id,
    12.0 AS expected_avg_temp,
    60.0 AS expected_avg_humidity,
    30.0 AS expected_avg_cloud,
    600.0 AS expected_avg_boundary_layer,
    6.0 AS expected_total_precip,
    7.0 AS expected_max_wind
),
actual AS (
  SELECT
    station_id,
    avg_temperature_2m,
    avg_relative_humidity_2m,
    avg_cloud_cover,
    avg_boundary_layer_height,
    total_precipitation,
    max_wind_speed_10m
  FROM `{project_id}.{analytics_dataset}.v_station_current_outlook`
  WHERE station_id = 'station_a'
)
SELECT
  'v_station_current_outlook__aggregations_are_correct' AS test_name,
  e.station_id AS entity_id,
  CONCAT('avg_temp:', CAST(e.expected_avg_temp AS STRING),
         ',avg_humidity:', CAST(e.expected_avg_humidity AS STRING),
         ',avg_cloud:', CAST(e.expected_avg_cloud AS STRING),
         ',avg_boundary:', CAST(e.expected_avg_boundary_layer AS STRING),
         ',total_precip:', CAST(e.expected_total_precip AS STRING),
         ',max_wind:', CAST(e.expected_max_wind AS STRING)) AS expected_value,
  CONCAT('avg_temp:', CAST(a.avg_temperature_2m AS STRING),
         ',avg_humidity:', CAST(a.avg_relative_humidity_2m AS STRING),
         ',avg_cloud:', CAST(a.avg_cloud_cover AS STRING),
         ',avg_boundary:', CAST(a.avg_boundary_layer_height AS STRING),
         ',total_precip:', CAST(a.total_precipitation AS STRING),
         ',max_wind:', CAST(a.max_wind_speed_10m AS STRING)) AS actual_value,
  'Weather aggregations do not match expected' AS reason
FROM expected e
LEFT JOIN actual a ON e.station_id = a.station_id
WHERE a.station_id IS NULL
   OR ABS(a.avg_temperature_2m - e.expected_avg_temp) > 0.1
   OR ABS(a.avg_relative_humidity_2m - e.expected_avg_humidity) > 0.1
   OR ABS(a.avg_cloud_cover - e.expected_avg_cloud) > 0.1
   OR ABS(a.avg_boundary_layer_height - e.expected_avg_boundary_layer) > 0.1
   OR ABS(a.total_precipitation - e.expected_total_precip) > 0.1
   OR ABS(a.max_wind_speed_10m - e.expected_max_wind) > 0.1;
