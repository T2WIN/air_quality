-- Assert: Latest PM2.5 is correctly attached to dispersion outlook
-- Station A has OpenAQ data → latest_pm25 should be 22.0
-- Station C has no OpenAQ data → latest_pm25 should be NULL

WITH expected AS (
  SELECT 'station_a' AS station_id, 22.0 AS expected_latest_pm25
  UNION ALL
  SELECT 'station_c' AS station_id, NULL AS expected_latest_pm25
),
actual AS (
  SELECT
    station_id,
    latest_pm25
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_dispersion_outlook`
  WHERE station_id IN ('station_a', 'station_c')
),
cardinality_check AS (
  SELECT
    a.station_id,
    COUNT(*) AS row_count
  FROM actual a
  GROUP BY a.station_id
  HAVING COUNT(*) != 3
)
SELECT
  'v_station_dispersion_outlook__latest_pm25_attached' AS test_name,
  e.station_id AS entity_id,
  CASE WHEN e.expected_latest_pm25 IS NULL THEN 'NULL' ELSE CAST(e.expected_latest_pm25 AS STRING) END AS expected_value,
  CASE WHEN a.latest_pm25 IS NULL THEN 'NULL' ELSE CAST(a.latest_pm25 AS STRING) END AS actual_value,
  'Latest PM2.5 mismatch or NULL handling incorrect' AS reason
FROM expected e
LEFT JOIN actual a ON e.station_id = a.station_id
WHERE a.station_id IS NULL
   OR (e.expected_latest_pm25 IS NULL AND a.latest_pm25 IS NOT NULL)
   OR (e.expected_latest_pm25 IS NOT NULL AND ABS(COALESCE(a.latest_pm25, -1) - e.expected_latest_pm25) > 0.01);