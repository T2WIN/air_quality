-- ============================================================
-- v_station_freshness: Data freshness metrics per station
-- Shows hours since last reading and staleness flags
-- Uses ${REFERENCE_TIMESTAMP} for testability
-- ============================================================

CREATE OR REPLACE VIEW `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_station_freshness` AS
WITH latest_readings AS (
  SELECT
    station_id,
    MAX(period_from_utc) AS latest_hour_utc,
    MAX(ingested_at) AS latest_ingested_at
  FROM `${PROJECT_ID}.${BQ_STAGING_DATASET}.v_openaq_deduped`
  GROUP BY station_id
),
freshness_calc AS (
  SELECT
    station_id,
    latest_hour_utc,
    latest_ingested_at,
    -- Hours since the data timestamp (reference - latest_hour)
    TIMESTAMP_DIFF(${REFERENCE_TIMESTAMP}, latest_hour_utc, HOUR) AS hours_since_data,
    -- Hours since ingestion (reference - ingested_at)
    TIMESTAMP_DIFF(${REFERENCE_TIMESTAMP}, latest_ingested_at, HOUR) AS hours_since_ingestion
  FROM latest_readings
)
SELECT
  fc.*,
  m.station_name,
  m.locality,
  m.country_code,
  m.latitude,
  m.longitude,
  -- Staleness flags (tunable thresholds)
  CASE WHEN fc.hours_since_data > 2 THEN TRUE ELSE FALSE END AS is_data_stale,
  CASE WHEN fc.hours_since_ingestion > 4 THEN TRUE ELSE FALSE END AS is_ingestion_stale
FROM freshness_calc fc
LEFT JOIN `${PROJECT_ID}.${BQ_RAW_DATASET}.station_metadata` m
  USING (station_id);
