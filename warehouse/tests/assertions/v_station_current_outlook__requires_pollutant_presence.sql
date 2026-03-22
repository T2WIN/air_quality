-- Assert: Stations with weather only and no pollutant basis do not appear
-- v_station_current_outlook should only show stations that have pollutant data
-- (station_b has pollutants but no weather, which is fine - it should still appear with NULL weather)
-- A station with ONLY weather and no pollutants should NOT appear

SELECT
  'v_station_current_outlook__requires_pollutant_presence' AS test_name,
  station_id AS entity_id,
  'should_not_appear' AS expected_value,
  'appears' AS actual_value,
  'Station should not appear without pollutant data' AS reason
FROM `{project_id}.{analytics_dataset}.v_station_current_outlook`
WHERE station_id NOT IN ('station_a', 'station_b');
