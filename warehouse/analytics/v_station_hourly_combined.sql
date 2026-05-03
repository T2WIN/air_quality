-- ============================================================
-- v_station_hourly_combined: Hourly pollutants LEFT JOINed with weather
-- Serves the time-series section
-- ============================================================

CREATE OR REPLACE VIEW `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_hourly_combined`
AS
WITH pollutants AS (
  SELECT
    station_id,
    openaq_location_id,
    period_from_utc AS hour,
    MAX(CASE WHEN pollutant = 'pm25' THEN value END) AS pm25_value,
    MAX(CASE WHEN pollutant = 'pm25' THEN unit END) AS pm25_unit,
    MAX(CASE WHEN pollutant = 'pm10' THEN value END) AS pm10_value,
    MAX(CASE WHEN pollutant = 'pm10' THEN unit END) AS pm10_unit,
    MAX(CASE WHEN pollutant = 'no2' THEN value END) AS no2_value,
    MAX(CASE WHEN pollutant = 'no2' THEN unit END) AS no2_unit
  FROM `${PROJECT_ID}.${BQ_STAGING_DATASET}.v_openaq_deduped`
  WHERE pollutant IN ('pm25', 'pm10', 'no2')
  GROUP BY station_id, openaq_location_id, period_from_utc
),
weather AS (
  SELECT
    station_id,
    valid_time AS hour,
    temperature_2m,
    relative_humidity_2m,
    surface_pressure,
    wind_speed_10m,
    wind_direction_10m,
    precipitation,
    cloud_cover,
    boundary_layer_height
  FROM `${PROJECT_ID}.${BQ_STAGING_DATASET}.v_weather_deduped`
)
SELECT
  p.station_id,
  p.hour AS hour_utc,
  p.pm25_value,
  p.pm10_value,
  p.no2_value,
  w.temperature_2m,
  w.relative_humidity_2m,
  w.wind_speed_10m,
  w.wind_direction_10m,
  w.precipitation,
  w.cloud_cover,
  w.boundary_layer_height
FROM pollutants AS p
LEFT JOIN weather AS w
  ON p.station_id = w.station_id AND p.hour = w.hour;
