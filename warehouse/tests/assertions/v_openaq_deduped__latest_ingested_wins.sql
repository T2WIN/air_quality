-- Assert: Latest ingested_at wins for duplicate dedup_keys
-- Returns rows if the wrong row (earlier ingested_at) appears in deduped view

WITH expected_latest AS (
  -- For the duplicate dedup_key '101_2026-03-22T10:00:00', 
  -- value 22.0 (ingested_at 10:00) should win over 99.0 (ingested_at 09:00)
  SELECT '101_2026-03-22T10:00:00' AS dedup_key, 22.0 AS expected_value
),
actual_rows AS (
  SELECT
    dedup_key,
    value AS actual_value
  FROM `{project_id}.{staging_dataset}.v_openaq_deduped`
  WHERE dedup_key = '101_2026-03-22T10:00:00'
)
SELECT
  'v_openaq_deduped__latest_ingested_wins' AS test_name,
  a.dedup_key AS entity_id,
  e.expected_value AS expected_value,
  a.actual_value AS actual_value,
  'Latest ingested_at row not selected' AS reason
FROM expected_latest e
LEFT JOIN actual_rows a ON e.dedup_key = a.dedup_key
WHERE a.actual_value IS NULL OR a.actual_value != e.expected_value;
