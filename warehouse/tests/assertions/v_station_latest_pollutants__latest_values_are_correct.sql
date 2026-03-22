-- Assert: Latest values are correctly selected and pivoted
-- For station_a, the latest readings are at 10:00 UTC: pm25=22, pm10=30, no2=45

WITH expected AS (
  SELECT
    'station_a' AS station_id,
    22.0 AS expected_pm25,
    30.0 AS expected_pm10,
    45.0 AS expected_no2
),
actual AS (
  SELECT
    station_id,
    pm25_value,
    pm10_value,
    no2_value
  FROM `{project_id}.{analytics_dataset}.v_station_latest_pollutants`
  WHERE station_id = 'station_a'
)
SELECT
  'v_station_latest_pollutants__latest_values_are_correct' AS test_name,
  e.station_id AS entity_id,
  CONCAT('pm25:', CAST(e.expected_pm25 AS STRING), ',pm10:', CAST(e.expected_pm10 AS STRING), ',no2:', CAST(e.expected_no2 AS STRING)) AS expected_value,
  CONCAT('pm25:', CAST(a.pm25_value AS STRING), ',pm10:', CAST(a.pm10_value AS STRING), ',no2:', CAST(a.no2_value AS STRING)) AS actual_value,
  'Latest pollutant values do not match expected' AS reason
FROM expected e
LEFT JOIN actual a ON e.station_id = a.station_id
WHERE a.station_id IS NULL
   OR a.pm25_value IS NULL OR a.pm25_value != e.expected_pm25
   OR a.pm10_value IS NULL OR a.pm10_value != e.expected_pm10
   OR a.no2_value IS NULL OR a.no2_value != e.expected_no2;
