-- Assert: Station metadata fields are correctly joined for expected stations
-- Each expected station should appear with non-NULL metadata fields

WITH expected_stations AS (
  SELECT 'station_a' AS station_id UNION ALL
  SELECT 'station_b'
),
actual AS (
  SELECT
    station_id,
    station_name,
    locality,
    country_code,
    latitude,
    longitude
  FROM `{project_id}.{analytics_dataset}.v_station_latest_pollutants`
)
SELECT
  'v_station_latest_pollutants__metadata_present' AS test_name,
  e.station_id AS entity_id,
  'metadata_present' AS expected_value,
  COALESCE(a.station_name, 'MISSING_OR_NULL') AS actual_value,
  'Expected station should appear with non-NULL metadata' AS reason
FROM expected_stations e
LEFT JOIN actual a ON e.station_id = a.station_id
WHERE a.station_id IS NULL
   OR a.station_name IS NULL
   OR a.locality IS NULL
   OR a.country_code IS NULL
   OR a.latitude IS NULL
   OR a.longitude IS NULL;
