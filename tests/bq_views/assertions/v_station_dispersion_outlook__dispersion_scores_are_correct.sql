-- Assert: Dispersion scores are correctly computed for all stations
-- Station A at 15:00: blh=200, wind=3, precip=0 → blh_score=0.133, wind_score=0.12, precip_score=0, score=0.095, category='poor'
-- Station A at 16:00: blh=1200, wind=15, precip=2 → blh_score=0.8, wind_score=0.6, precip_score=0.667, score=0.697, category='good'
-- Station A at 17:00: blh=800, wind=8, precip=0.5 → blh_score=0.533, wind_score=0.32, precip_score=0.167, score=0.367, category='fair'

WITH expected AS (
  SELECT
    'station_a' AS station_id,
    TIMESTAMP '2026-03-22 15:00:00 UTC' AS valid_time,
    0.133 AS expected_blh_score,
    0.12 AS expected_wind_score,
    0.0 AS expected_precip_score,
    0.095 AS expected_dispersion_score,
    'poor' AS expected_category
  UNION ALL
  SELECT
    'station_a' AS station_id,
    TIMESTAMP '2026-03-22 16:00:00 UTC' AS valid_time,
    0.8 AS expected_blh_score,
    0.6 AS expected_wind_score,
    0.667 AS expected_precip_score,
    0.697 AS expected_dispersion_score,
    'good' AS expected_category
  UNION ALL
  SELECT
    'station_a' AS station_id,
    TIMESTAMP '2026-03-22 17:00:00 UTC' AS valid_time,
    0.533 AS expected_blh_score,
    0.32 AS expected_wind_score,
    0.167 AS expected_precip_score,
    0.367 AS expected_dispersion_score,
    'fair' AS expected_category
),
actual AS (
  SELECT
    station_id,
    valid_time,
    blh_score,
    wind_score,
    precip_score,
    dispersion_score,
    outlook_category
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_dispersion_outlook`
)
SELECT
  'v_station_dispersion_outlook__dispersion_scores_are_correct' AS test_name,
  e.station_id AS entity_id,
  CONCAT('blh:', CAST(e.expected_blh_score AS STRING),
         ',wind:', CAST(e.expected_wind_score AS STRING),
         ',precip:', CAST(e.expected_precip_score AS STRING),
         ',score:', CAST(e.expected_dispersion_score AS STRING),
         ',cat:', e.expected_category) AS expected_value,
  CONCAT('blh:', CAST(a.blh_score AS STRING),
         ',wind:', CAST(a.wind_score AS STRING),
         ',precip:', CAST(a.precip_score AS STRING),
         ',score:', CAST(a.dispersion_score AS STRING),
         ',cat:', a.outlook_category) AS actual_value,
  'Dispersion scores do not match expected values' AS reason
FROM expected e
LEFT JOIN actual a ON e.station_id = a.station_id AND e.valid_time = a.valid_time
WHERE a.station_id IS NULL
   OR ABS(a.blh_score - e.expected_blh_score) > 0.01
   OR ABS(a.wind_score - e.expected_wind_score) > 0.01
   OR ABS(a.precip_score - e.expected_precip_score) > 0.01
   OR ABS(a.dispersion_score - e.expected_dispersion_score) > 0.01
   OR a.outlook_category != e.expected_category;