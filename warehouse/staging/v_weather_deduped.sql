-- ============================================================
-- v_weather_deduped: Deduplicated weather forecast data
-- Returns only the latest ingested_at row per station_id + valid_time
-- ============================================================

CREATE OR REPLACE VIEW `${PROJECT_ID}.${BQ_STAGING_DATASET}.v_weather_deduped` AS
SELECT * EXCEPT (rn)
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY station_id, valid_time
      ORDER BY ingested_at DESC
    ) AS rn
  FROM `${PROJECT_ID}.${BQ_RAW_DATASET}.weather_forecasts`
)
WHERE rn = 1;
