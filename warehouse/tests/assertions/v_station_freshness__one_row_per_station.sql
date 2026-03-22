-- Assert: One row per station in v_station_freshness
-- Returns rows if any station has multiple rows

SELECT
  'v_station_freshness__one_row_per_station' AS test_name,
  station_id AS entity_id,
  '1' AS expected_value,
  CAST(COUNT(*) AS STRING) AS actual_value,
  'Station has more than one row' AS reason
FROM `{project_id}.{analytics_dataset}.v_station_freshness`
GROUP BY station_id
HAVING COUNT(*) > 1;
