-- ============================================================
-- v_weather_deduped: Deduplicated weather forecast data
-- Returns only the latest ingested_at row per station_id + valid_time
-- ============================================================

CREATE OR REPLACE VIEW `{project_id}.{staging_dataset}.v_weather_deduped` AS
SELECT
  * EXCEPT(rn)
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY station_id, valid_time
      ORDER BY ingested_at DESC
    ) AS rn
  FROM `{project_id}.{raw_dataset}.weather_forecasts`
)
WHERE rn = 1;
