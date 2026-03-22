-- Assert: One row per source in v_ingestion_overview
-- Returns rows if any source has multiple rows

SELECT
  'v_ingestion_overview__one_row_per_source' AS test_name,
  source AS entity_id,
  '1' AS expected_value,
  CAST(COUNT(*) AS STRING) AS actual_value,
  'Source has more than one row' AS reason
FROM `{project_id}.{analytics_dataset}.v_ingestion_overview`
GROUP BY source
HAVING COUNT(*) > 1;
