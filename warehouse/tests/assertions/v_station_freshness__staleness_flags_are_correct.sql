-- Assert: Staleness flags are correct based on reference timestamp
-- Reference timestamp is 2026-03-22 14:00:00 UTC
-- station_a latest data at 10:00 -> 4 hours ago -> is_data_stale = TRUE (>2h threshold)
-- station_a latest ingestion at 10:00 -> 4 hours ago -> is_ingestion_stale = FALSE (>=4h threshold means =4 is NOT stale)

WITH expected AS (
  SELECT
    'station_a' AS station_id,
    TRUE AS expected_data_stale,
    FALSE AS expected_ingestion_stale,
    4 AS expected_hours_since_data
),
actual AS (
  SELECT
    station_id,
    is_data_stale,
    is_ingestion_stale,
    hours_since_data
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_freshness`
  WHERE station_id = 'station_a'
)
SELECT
  'v_station_freshness__staleness_flags_are_correct' AS test_name,
  e.station_id AS entity_id,
  CONCAT('data_stale:', CAST(e.expected_data_stale AS STRING), ',ingestion_stale:', CAST(e.expected_ingestion_stale AS STRING)) AS expected_value,
  CONCAT('data_stale:', CAST(a.is_data_stale AS STRING), ',ingestion_stale:', CAST(a.is_ingestion_stale AS STRING), ',hours_since:', CAST(a.hours_since_data AS STRING)) AS actual_value,
  'Staleness flags do not match expected' AS reason
FROM expected e
LEFT JOIN actual a ON e.station_id = a.station_id
WHERE a.station_id IS NULL
   OR a.is_data_stale != e.expected_data_stale
   OR a.is_ingestion_stale != e.expected_ingestion_stale
   OR a.hours_since_data != e.expected_hours_since_data;
