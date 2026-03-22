-- ============================================================
-- v_station_current_outlook: Current conditions and 3h weather outlook
-- Shows latest pollutants + weather forecasts for next 3 hours
-- ============================================================

CREATE OR REPLACE VIEW `{project_id}.{analytics_dataset}.v_station_current_outlook` AS
WITH ranked_pollutants AS (
  -- Rank pollutant readings by period_from_utc DESC, ingested_at DESC
  -- to get the latest reading per station per pollutant
  SELECT
    station_id,
    pollutant,
    value,
    period_from_utc,
    ROW_NUMBER() OVER (
      PARTITION BY station_id, pollutant
      ORDER BY period_from_utc DESC, ingested_at DESC
    ) AS rn
  FROM `{project_id}.{staging_dataset}.v_openaq_deduped`
  WHERE pollutant IN ('pm25', 'pm10', 'no2')
),
latest_pollutants AS (
  -- Pivot the latest readings (rn=1) into columns
  SELECT
    station_id,
    MAX(CASE WHEN pollutant = 'pm25' THEN value END) AS pm25_value,
    MAX(CASE WHEN pollutant = 'pm10' THEN value END) AS pm10_value,
    MAX(CASE WHEN pollutant = 'no2' THEN value END) AS no2_value,
    MAX(period_from_utc) AS latest_reading_time
  FROM ranked_pollutants
  WHERE rn = 1
  GROUP BY station_id
),
weather_basis AS (
  -- Find the latest ingested_at timestamp for weather per station
  SELECT
    station_id,
    MAX(ingested_at) AS latest_weather_ingested_at
  FROM `{project_id}.{staging_dataset}.v_weather_deduped`
  GROUP BY station_id
),
outlook_weather AS (
  -- Aggregate weather for the 3h window after the weather ingestion time
  -- valid_time > ingested_at AND valid_time <= ingested_at + 3 hours
  SELECT
    wb.station_id,
    wb.latest_weather_ingested_at,
    AVG(w.temperature_2m) AS avg_temperature_2m,
    AVG(w.relative_humidity_2m) AS avg_relative_humidity_2m,
    AVG(w.cloud_cover) AS avg_cloud_cover,
    AVG(w.boundary_layer_height) AS avg_boundary_layer_height,
    SUM(w.precipitation) AS total_precipitation,
    MAX(w.wind_speed_10m) AS max_wind_speed_10m,
    COUNT(w.valid_time) AS forecast_hours_count
  FROM weather_basis wb
  LEFT JOIN `{project_id}.{staging_dataset}.v_weather_deduped` w
    ON wb.station_id = w.station_id
    AND w.valid_time > wb.latest_weather_ingested_at
    AND w.valid_time <= TIMESTAMP_ADD(wb.latest_weather_ingested_at, INTERVAL 3 HOUR)
  GROUP BY wb.station_id, wb.latest_weather_ingested_at
)
SELECT
  lp.station_id,
  -- Current pollutant readings
  lp.pm25_value,
  lp.pm10_value,
  lp.no2_value,
  lp.latest_reading_time,
  -- Weather outlook (may be NULL if no weather data)
  ow.latest_weather_ingested_at,
  ow.avg_temperature_2m,
  ow.avg_relative_humidity_2m,
  ow.avg_cloud_cover,
  ow.avg_boundary_layer_height,
  ow.total_precipitation,
  ow.max_wind_speed_10m,
  -- Count of forecast hours in the 3h window (0 if no weather data)
  COALESCE(ow.forecast_hours_count, 0) AS forecast_hours_count,
  -- Station metadata
  m.station_name,
  m.locality,
  m.country_code,
  m.latitude,
  m.longitude
FROM latest_pollutants lp
LEFT JOIN outlook_weather ow
  ON lp.station_id = ow.station_id
LEFT JOIN `{project_id}.{raw_dataset}.station_metadata` m
  ON lp.station_id = m.station_id;
