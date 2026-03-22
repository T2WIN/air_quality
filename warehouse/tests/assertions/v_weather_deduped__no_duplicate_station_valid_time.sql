-- Assert: No duplicate (station_id, valid_time) pairs in deduped view
-- Returns rows if duplicates exist

SELECT
  'v_weather_deduped__no_duplicate_station_valid_time' AS test_name,
  station_id AS entity_id,
  NULL AS expected_value,
  CAST(COUNT(*) AS STRING) AS actual_value,
  'Duplicate station-valid_time pairs found' AS reason
FROM `{project_id}.{staging_dataset}.v_weather_deduped`
GROUP BY station_id, valid_time
HAVING COUNT(*) > 1;
