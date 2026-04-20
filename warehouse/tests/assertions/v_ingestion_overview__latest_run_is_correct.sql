-- Assert: Latest run per source is correctly identified
-- For openaq: latest run is run_1 (success, started 10:00)
-- For open-meteo: latest run is weather_run_1 (success, started 10:00)

WITH expected AS (
  SELECT 'openaq' AS source,
         'run_1' AS expected_run_id,
         'success' AS expected_status,
         TIMESTAMP '2026-03-22 10:00:00 UTC' AS expected_started_at,
         500 AS expected_records
  UNION ALL
  SELECT 'open-meteo',
         'weather_run_1',
         'success',
         TIMESTAMP '2026-03-22 10:00:00 UTC',
         96
),
actual AS (
  SELECT
    source,
    latest_run_id,
    latest_run_status,
    latest_run_started_at,
    latest_run_records_written
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_ingestion_overview`
)
SELECT
  'v_ingestion_overview__latest_run_is_correct' AS test_name,
  e.source AS entity_id,
  CONCAT('run_id:', e.expected_run_id,
         ',status:', e.expected_status,
         ',started:', CAST(e.expected_started_at AS STRING),
         ',records:', CAST(e.expected_records AS STRING)) AS expected_value,
  CONCAT('run_id:', COALESCE(a.latest_run_id, 'NULL'),
         ',status:', COALESCE(a.latest_run_status, 'NULL'),
         ',started:', COALESCE(CAST(a.latest_run_started_at AS STRING), 'NULL'),
         ',records:', COALESCE(CAST(a.latest_run_records_written AS STRING), 'NULL')) AS actual_value,
  'Latest run fields do not match expected' AS reason
FROM expected e
LEFT JOIN actual a ON e.source = a.source
WHERE a.source IS NULL
   OR a.latest_run_id != e.expected_run_id
   OR a.latest_run_status != e.expected_status
   OR a.latest_run_started_at != e.expected_started_at
   OR a.latest_run_records_written != e.expected_records;