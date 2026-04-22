-- Assert: Latest ingested_at wins for duplicate (station_id, valid_time)
-- For station_a, valid_time 11:00, temperature 10.0 (ingested_at 10:00) should win over -99.0 (ingested_at 09:00)

WITH expected AS (
  SELECT 'station_a' AS station_id, TIMESTAMP '2026-03-22 11:00:00 UTC' AS valid_time, 10.0 AS expected_temp
),
actual AS (
  SELECT
    station_id,
    valid_time,
    temperature_2m AS actual_temp
  FROM `${PROJECT_ID}.${BQ_STAGING_DATASET}.v_weather_deduped`
  WHERE station_id = 'station_a' AND valid_time = TIMESTAMP '2026-03-22 11:00:00 UTC'
)
SELECT
  'v_weather_deduped__latest_ingested_wins' AS test_name,
  e.station_id AS entity_id,
  CAST(e.expected_temp AS STRING) AS expected_value,
  CAST(a.actual_temp AS STRING) AS actual_value,
  'Latest ingested_at weather row not selected' AS reason
FROM expected e
LEFT JOIN actual a ON e.station_id = a.station_id AND e.valid_time = a.valid_time
WHERE a.actual_temp IS NULL OR a.actual_temp != e.expected_temp;
