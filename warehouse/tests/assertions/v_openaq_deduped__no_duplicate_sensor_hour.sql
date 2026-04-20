-- Assert: No duplicate (openaq_sensor_id, period_from_utc) pairs in deduped view
-- Returns rows if duplicates exist

SELECT
  'v_openaq_deduped__no_duplicate_sensor_hour' AS test_name,
  CAST(openaq_sensor_id AS STRING) AS entity_id,
  NULL AS expected_value,
  CAST(COUNT(*) AS STRING) AS actual_value,
  'Duplicate sensor-hour pairs found' AS reason
FROM `${PROJECT_ID}.${BQ_STAGING_DATASET}.v_openaq_deduped`
GROUP BY openaq_sensor_id, period_from_utc
HAVING COUNT(*) > 1;
