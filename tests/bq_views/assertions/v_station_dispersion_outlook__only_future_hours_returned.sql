-- Assert: Only future hours (valid_time > reference_timestamp) are returned
-- reference_timestamp = 2026-03-22 14:00:00 UTC
-- Station A should have 15:00, 16:00, 17:00 (3 rows)
-- Station C should have 15:00, 16:00 (2 rows)

WITH expected_future_hours AS (
  SELECT station_id, valid_time FROM UNNEST([
    STRUCT('station_a' AS station_id, TIMESTAMP '2026-03-22 15:00:00 UTC' AS valid_time),
    ('station_a', TIMESTAMP '2026-03-22 16:00:00 UTC'),
    ('station_a', TIMESTAMP '2026-03-22 17:00:00 UTC'),
    ('station_c', TIMESTAMP '2026-03-22 15:00:00 UTC'),
    ('station_c', TIMESTAMP '2026-03-22 16:00:00 UTC')
  ])
),
actual_future_hours AS (
  SELECT station_id, valid_time
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_dispersion_outlook`
),
non_future AS (
  SELECT station_id, valid_time
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_dispersion_outlook`
  WHERE valid_time <= ${REFERENCE_TIMESTAMP}
)
SELECT
  'v_station_dispersion_outlook__only_future_hours_returned' AS test_name,
  nf.station_id AS entity_id,
  'no rows with valid_time <= reference_timestamp' AS expected_value,
  CONCAT(nf.station_id, ':', CAST(nf.valid_time AS STRING)) AS actual_value,
  'Found row with valid_time <= reference timestamp' AS reason
FROM non_future nf;