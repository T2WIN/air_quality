-- Assert: One row per (station_id, hour_utc) in v_station_hourly_wide
-- Returns rows if duplicates exist

SELECT
  'v_station_hourly_wide__one_row_per_station_hour' AS test_name,
  CONCAT(station_id, ':', CAST(hour_utc AS STRING)) AS entity_id,
  NULL AS expected_value,
  CAST(COUNT(*) AS STRING) AS actual_value,
  'Duplicate station-hour pairs found' AS reason
FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_hourly_wide`
GROUP BY station_id, hour_utc
HAVING COUNT(*) > 1;
