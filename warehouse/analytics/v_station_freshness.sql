-- ============================================================
-- v_station_freshness: Data freshness metrics per station
-- Shows hours since last reading and staleness flags
-- Uses {reference_timestamp} placeholder for testability
-- ============================================================

CREATE OR REPLACE VIEW `{project_id}.{analytics_dataset}.v_station_freshness` AS
WITH latest_readings AS (
  SELECT
    station_id,
    MAX(period_from_utc) AS latest_hour_utc,
    MAX(ingested_at) AS latest_ingested_at
  FROM `{project_id}.{staging_dataset}.v_openaq_deduped`
  GROUP BY station_id
),
freshness_calc AS (
  SELECT
    station_id,
    latest_hour_utc,
    latest_ingested_at,
    -- Hours since the data timestamp (reference - latest_hour)
    TIMESTAMP_DIFF({reference_timestamp}, latest_hour_utc, HOUR) AS hours_since_data,
    -- Hours since ingestion (reference - ingested_at)
    TIMESTAMP_DIFF({reference_timestamp}, latest_ingested_at, HOUR) AS hours_since_ingestion
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
LEFT JOIN `{project_id}.{raw_dataset}.station_metadata` m
  USING (station_id);
