-- ============================================================
-- Core fixture data for view tests
-- Deterministic dataset with fixed timestamps
-- Base reference time: 2026-03-22 14:00:00 UTC
-- ============================================================

-- ============================================================
-- 1. Station Metadata (3 stations)
-- ============================================================

INSERT INTO `{project_id}.{raw_dataset}.station_metadata` (
  station_id, openaq_location_id, station_name, locality, country_code,
  country_name, latitude, longitude, timezone, is_mobile, is_monitor,
  loaded_at
) VALUES
  (
    'station_a', 1, 'Station A - Central', 'Berlin', 'DE', 'Germany',
    52.5200, 13.4050, 'Europe/Berlin', FALSE, TRUE,
    TIMESTAMP '2026-03-22 10:00:00 UTC'
  ),
  (
    'station_b', 2, 'Station B - North', 'Hamburg', 'DE', 'Germany',
    53.5511, 9.9937, 'Europe/Berlin', FALSE, TRUE,
    TIMESTAMP '2026-03-22 10:00:00 UTC'
  ),
  (
    'station_c', 3, 'Station C - Weather Only', 'Munich', 'DE', 'Germany',
    48.1351, 11.5820, 'Europe/Berlin', FALSE, TRUE,
    TIMESTAMP '2026-03-22 10:00:00 UTC'
  );

-- ============================================================
-- 2. Station Sensors (3 sensors per station: pm25, pm10, no2)
-- ============================================================

INSERT INTO `{project_id}.{raw_dataset}.station_sensors` (
  station_id, openaq_location_id, openaq_sensor_id,
  parameter_id, parameter_name, parameter_display_name, parameter_units,
  loaded_at
) VALUES
  -- Station A sensors
  ('station_a', 1, 101, 1, 'pm25', 'PM2.5', 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC'),
  ('station_a', 1, 102, 2, 'pm10', 'PM10', 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC'),
  ('station_a', 1, 103, 3, 'no2', 'NO2', 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC'),
  -- Station B sensors
  ('station_b', 2, 201, 1, 'pm25', 'PM2.5', 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC'),
  ('station_b', 2, 202, 2, 'pm10', 'PM10', 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC'),
  ('station_b', 2, 203, 3, 'no2', 'NO2', 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC');

-- ============================================================
-- 3. OpenAQ Hourly Data
-- ============================================================

INSERT INTO `{project_id}.{raw_dataset}.openaq_hourly` (
  ingested_at, run_id, station_id, openaq_location_id, openaq_sensor_id,
  pollutant, value, unit, period_from_utc, period_to_utc, dedup_key
) VALUES
  -- Station A: Hour 07:00 - PM2.5 (HIGHER value, older - tests "latest not max")
  -- This reading has value=50 at 07:00, but latest at 10:00 is value=22
  -- The view should return 22 (latest), not 50 (max)
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_a', 1, 101,
   'pm25', 50.0, 'µg/m³', TIMESTAMP '2026-03-22 07:00:00 UTC', TIMESTAMP '2026-03-22 08:00:00 UTC',
   '101_2026-03-22T07:00:00'),

  -- Station A: Hour 08:00 - PM2.5 (baseline)
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_a', 1, 101,
   'pm25', 15.0, 'µg/m³', TIMESTAMP '2026-03-22 08:00:00 UTC', TIMESTAMP '2026-03-22 09:00:00 UTC',
   '101_2026-03-22T08:00:00'),

  -- Station A: Hour 09:00 - all 3 pollutants
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_a', 1, 101,
   'pm25', 18.0, 'µg/m³', TIMESTAMP '2026-03-22 09:00:00 UTC', TIMESTAMP '2026-03-22 10:00:00 UTC',
   '101_2026-03-22T09:00:00'),
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_a', 1, 102,
   'pm10', 25.0, 'µg/m³', TIMESTAMP '2026-03-22 09:00:00 UTC', TIMESTAMP '2026-03-22 10:00:00 UTC',
   '102_2026-03-22T09:00:00'),
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_a', 1, 103,
   'no2', 40.0, 'µg/m³', TIMESTAMP '2026-03-22 09:00:00 UTC', TIMESTAMP '2026-03-22 10:00:00 UTC',
   '103_2026-03-22T09:00:00'),

  -- Station A: Hour 10:00 - all 3 pollutants (LATEST for station_a)
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_a', 1, 101,
   'pm25', 22.0, 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC', TIMESTAMP '2026-03-22 11:00:00 UTC',
   '101_2026-03-22T10:00:00'),
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_a', 1, 102,
   'pm10', 30.0, 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC', TIMESTAMP '2026-03-22 11:00:00 UTC',
   '102_2026-03-22T10:00:00'),
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_a', 1, 103,
   'no2', 45.0, 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC', TIMESTAMP '2026-03-22 11:00:00 UTC',
   '103_2026-03-22T10:00:00'),

  -- Station A: DEDUP TEST - duplicate key with earlier ingested_at
  -- This should be filtered out in deduped view (later ingested_at wins)
  (TIMESTAMP '2026-03-22 09:00:00 UTC', 'run_0', 'station_a', 1, 101,
   'pm25', 99.0, 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC', TIMESTAMP '2026-03-22 11:00:00 UTC',
   '101_2026-03-22T10:00:00'),

  -- Station B: Hour 08:00 - only pm25
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_b', 2, 201,
   'pm25', 12.0, 'µg/m³', TIMESTAMP '2026-03-22 08:00:00 UTC', TIMESTAMP '2026-03-22 09:00:00 UTC',
   '201_2026-03-22T08:00:00'),

  -- Station B: Hour 09:00 - pm25 and pm10 only (missing no2)
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_b', 2, 201,
   'pm25', 14.0, 'µg/m³', TIMESTAMP '2026-03-22 09:00:00 UTC', TIMESTAMP '2026-03-22 10:00:00 UTC',
   '201_2026-03-22T09:00:00'),
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_b', 2, 202,
   'pm10', 20.0, 'µg/m³', TIMESTAMP '2026-03-22 09:00:00 UTC', TIMESTAMP '2026-03-22 10:00:00 UTC',
   '202_2026-03-22T09:00:00'),

  -- Station B: Hour 10:00 - all 3 pollutants (LATEST for station_b)
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_b', 2, 201,
   'pm25', 16.0, 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC', TIMESTAMP '2026-03-22 11:00:00 UTC',
   '201_2026-03-22T10:00:00'),
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_b', 2, 202,
   'pm10', 22.0, 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC', TIMESTAMP '2026-03-22 11:00:00 UTC',
   '202_2026-03-22T10:00:00'),
  (TIMESTAMP '2026-03-22 10:00:00 UTC', 'run_1', 'station_b', 2, 203,
   'no2', 35.0, 'µg/m³', TIMESTAMP '2026-03-22 10:00:00 UTC', TIMESTAMP '2026-03-22 11:00:00 UTC',
   '203_2026-03-22T10:00:00');

-- ============================================================
-- 4. Weather Forecasts
-- ============================================================

INSERT INTO `{project_id}.{raw_dataset}.weather_forecasts` (
  station_id, run_id, latitude, longitude, valid_time,
  temperature_2m, relative_humidity_2m, surface_pressure,
  wind_speed_10m, wind_direction_10m, precipitation,
  cloud_cover, boundary_layer_height, ingested_at, dedup_key
) VALUES
  -- Station A: ingested_at = 10:00, forecast batch with +1h, +2h, +3h, +4h
  ('station_a', 'weather_run_1', 52.5200, 13.4050,
   TIMESTAMP '2026-03-22 11:00:00 UTC',
   10.0, 65.0, 1013.0, 5.0, 180.0, 1.0, 20.0, 500.0,
   TIMESTAMP '2026-03-22 10:00:00 UTC', 'station_a_2026-03-22T11:00:00'),

  ('station_a', 'weather_run_1', 52.5200, 13.4050,
   TIMESTAMP '2026-03-22 12:00:00 UTC',
   12.0, 60.0, 1012.0, 7.0, 190.0, 2.0, 30.0, 600.0,
   TIMESTAMP '2026-03-22 10:00:00 UTC', 'station_a_2026-03-22T12:00:00'),

  ('station_a', 'weather_run_1', 52.5200, 13.4050,
   TIMESTAMP '2026-03-22 13:00:00 UTC',
   14.0, 55.0, 1011.0, 6.0, 200.0, 3.0, 40.0, 700.0,
   TIMESTAMP '2026-03-22 10:00:00 UTC', 'station_a_2026-03-22T13:00:00'),

  -- +4h forecast (should be EXCLUDED from 3h outlook)
  ('station_a', 'weather_run_1', 52.5200, 13.4050,
   TIMESTAMP '2026-03-22 14:00:00 UTC',
   99.0, 99.0, 999.0, 50.0, 270.0, 100.0, 99.0, 999.0,
   TIMESTAMP '2026-03-22 10:00:00 UTC', 'station_a_2026-03-22T14:00:00'),

  -- DEDUP TEST: Station A has duplicate valid_time with earlier ingested_at
  ('station_a', 'weather_run_0', 52.5200, 13.4050,
   TIMESTAMP '2026-03-22 11:00:00 UTC',
    -99.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    TIMESTAMP '2026-03-22 09:00:00 UTC', 'station_a_2026-03-22T11:00:00'),

  -- Station C: Weather-only station (has weather forecasts, no AQ data)
  ('station_c', 'weather_run_1', 48.1351, 11.5820,
   TIMESTAMP '2026-03-22 11:00:00 UTC',
   8.0, 70.0, 1015.0, 4.0, 160.0, 0.5, 15.0, 400.0,
   TIMESTAMP '2026-03-22 10:00:00 UTC', 'station_c_2026-03-22T11:00:00'),

  ('station_c', 'weather_run_1', 48.1351, 11.5820,
   TIMESTAMP '2026-03-22 12:00:00 UTC',
   10.0, 65.0, 1014.0, 5.0, 170.0, 1.0, 20.0, 450.0,
   TIMESTAMP '2026-03-22 10:00:00 UTC', 'station_c_2026-03-22T12:00:00'),

  ('station_c', 'weather_run_1', 48.1351, 11.5820,
   TIMESTAMP '2026-03-22 13:00:00 UTC',
   12.0, 60.0, 1013.0, 6.0, 180.0, 1.5, 25.0, 500.0,
   TIMESTAMP '2026-03-22 10:00:00 UTC', 'station_c_2026-03-22T13:00:00');

-- ============================================================
-- 5. Ingestion Log (for testing v_ingestion_overview)
-- ============================================================

INSERT INTO `{project_id}.{raw_dataset}.ingestion_log` (
  run_id, source, status, run_started_at, run_finished_at,
  duration_seconds, records_written, api_calls, api_errors,
  error_message, ingested_at
) VALUES
  -- OpenAQ: recent success (within 24h of reference 14:00)
  ('run_1', 'openaq', 'success',
   TIMESTAMP '2026-03-22 10:00:00 UTC', TIMESTAMP '2026-03-22 10:05:00 UTC',
   300.0, 500, 10, 0,
   NULL, TIMESTAMP '2026-03-22 10:05:00 UTC'),

  -- OpenAQ: recent error (within 24h)
  ('run_error', 'openaq', 'error',
   TIMESTAMP '2026-03-22 09:00:00 UTC', TIMESTAMP '2026-03-22 09:01:00 UTC',
   60.0, 0, 1, 1,
   'API rate limit exceeded', TIMESTAMP '2026-03-22 09:01:00 UTC'),

  -- OpenAQ: old run (outside 24h window)
  ('run_old', 'openaq', 'success',
   TIMESTAMP '2026-03-21 08:00:00 UTC', TIMESTAMP '2026-03-21 08:05:00 UTC',
   300.0, 400, 8, 0,
   NULL, TIMESTAMP '2026-03-21 08:05:00 UTC'),

  -- Open-Meteo: recent success
  ('weather_run_1', 'open-meteo', 'success',
   TIMESTAMP '2026-03-22 10:00:00 UTC', TIMESTAMP '2026-03-22 10:02:00 UTC',
   120.0, 96, 2, 0,
   NULL, TIMESTAMP '2026-03-22 10:02:00 UTC');