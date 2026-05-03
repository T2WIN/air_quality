-- ============================================================
-- v_station_dispersion_outlook: Forward-looking dispersion outlook
-- Future forecast hours with computed dispersion index
-- ============================================================

CREATE OR REPLACE VIEW `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_dispersion_outlook`
AS
WITH future_weather AS (
  SELECT
    w.station_id,
    w.valid_time,
    w.latitude,
    w.longitude,
    w.temperature_2m,
    w.relative_humidity_2m,
    w.surface_pressure,
    w.wind_speed_10m,
    w.wind_direction_10m,
    w.precipitation,
    w.cloud_cover,
    w.boundary_layer_height,
    COALESCE(
      LEAST(GREATEST(w.boundary_layer_height / 1500.0, 0), 1),
      0
    ) AS blh_score,
    COALESCE(
      LEAST(GREATEST(w.wind_speed_10m / 25.0, 0), 1),
      0
    ) AS wind_score,
    COALESCE(
      LEAST(GREATEST(w.precipitation / 3.0, 0), 1),
      0
    ) AS precip_score
  FROM `${PROJECT_ID}.${BQ_STAGING_DATASET}.v_weather_deduped` AS w
  WHERE w.valid_time > ${REFERENCE_TIMESTAMP}
),
dispersion AS (
  SELECT
    station_id,
    valid_time,
    latitude,
    longitude,
    temperature_2m,
    relative_humidity_2m,
    surface_pressure,
    wind_speed_10m,
    wind_direction_10m,
    precipitation,
    cloud_cover,
    boundary_layer_height,
    blh_score,
    wind_score,
    precip_score,
    ROUND(0.40 * blh_score + 0.35 * wind_score + 0.25 * precip_score, 2) AS dispersion_score,
    CASE
      WHEN 0.40 * blh_score + 0.35 * wind_score + 0.25 * precip_score < 0.30 THEN 'poor'
      WHEN 0.40 * blh_score + 0.35 * wind_score + 0.25 * precip_score <= 0.55 THEN 'fair'
      ELSE 'good'
    END AS outlook_category
  FROM future_weather
),
latest_pm AS (
  SELECT
    station_id,
    pm25_value AS latest_pm25,
    pm25_unit,
    pm25_time AS latest_pm25_time
  FROM `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_latest_pollutants`
)
SELECT
  d.station_id,
  d.valid_time,
  d.latitude,
  d.longitude,
  d.temperature_2m,
  d.relative_humidity_2m,
  d.surface_pressure,
  d.wind_speed_10m,
  d.wind_direction_10m,
  d.precipitation,
  d.cloud_cover,
  d.boundary_layer_height,
  d.blh_score,
  d.wind_score,
  d.precip_score,
  d.dispersion_score,
  d.outlook_category,
  p.latest_pm25,
  p.pm25_unit,
  p.latest_pm25_time,
  m.station_name,
  m.locality,
  m.country_code
FROM dispersion AS d
LEFT JOIN latest_pm AS p ON d.station_id = p.station_id
LEFT JOIN `${PROJECT_ID}.${BQ_RAW_DATASET}.station_metadata` AS m
  ON d.station_id = m.station_id;
