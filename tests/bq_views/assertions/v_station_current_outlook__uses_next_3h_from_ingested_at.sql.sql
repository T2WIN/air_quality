-- Assert: v_station_current_outlook only includes weather within (ingested_at, ingested_at + 3h]
-- The +4h forecast (14:00) should be excluded

SELECT
  'v_station_current_outlook__next_3h_only' AS test_name,
  station_id AS entity_id,
  '3' AS expected_value,
  CAST(forecast_hours_count AS STRING) AS actual_value,
  'Forecast includes hours beyond 3h window' AS reason
FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_current_outlook`
WHERE station_id = 'station_a'
  AND forecast_hours_count != 3;
