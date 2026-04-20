-- Assert: One row per station in v_station_current_outlook
-- Returns rows if any station has multiple rows

SELECT
  'v_station_current_outlook__one_row_per_station' AS test_name,
  station_id AS entity_id,
  '1' AS expected_value,
  CAST(COUNT(*) AS STRING) AS actual_value,
  'Station has more than one row' AS reason
FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_current_outlook`
GROUP BY station_id
HAVING COUNT(*) > 1;
