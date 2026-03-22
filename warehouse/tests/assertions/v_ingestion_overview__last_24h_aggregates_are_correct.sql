-- Assert: 24h aggregates are correct per source
-- Reference timestamp: 2026-03-22 14:00:00 UTC
-- 24h window: > 2026-03-21 14:00:00 UTC
-- openaq: 2 runs (run_1 + run_error), 1 success, 1 error, 500 total records
-- open-meteo: 1 run (weather_run_1), 1 success, 0 errors, 96 total records

WITH expected AS (
  SELECT 'openaq' AS source,
         2 AS expected_runs,
         1 AS expected_successes,
         1 AS expected_errors,
         500 AS expected_records
  UNION ALL
  SELECT 'open-meteo', 1, 1, 0, 96
),
actual AS (
  SELECT
    source,
    runs_24h,
    successes_24h,
    errors_24h,
    total_records_24h
  FROM `{project_id}.{analytics_dataset}.v_ingestion_overview`
)
SELECT
  'v_ingestion_overview__last_24h_aggregates_are_correct' AS test_name,
  e.source AS entity_id,
  CONCAT('runs:', CAST(e.expected_runs AS STRING),
         ',successes:', CAST(e.expected_successes AS STRING),
         ',errors:', CAST(e.expected_errors AS STRING),
         ',records:', CAST(e.expected_records AS STRING)) AS expected_value,
  CONCAT('runs:', COALESCE(CAST(a.runs_24h AS STRING), 'NULL'),
         ',successes:', COALESCE(CAST(a.successes_24h AS STRING), 'NULL'),
         ',errors:', COALESCE(CAST(a.errors_24h AS STRING), 'NULL'),
         ',records:', COALESCE(CAST(a.total_records_24h AS STRING), 'NULL')) AS actual_value,
  '24h aggregates do not match expected' AS reason
FROM expected e
LEFT JOIN actual a ON e.source = a.source
WHERE a.source IS NULL
   OR a.runs_24h != e.expected_runs
   OR a.successes_24h != e.expected_successes
   OR a.errors_24h != e.expected_errors
   OR a.total_records_24h != e.expected_records;