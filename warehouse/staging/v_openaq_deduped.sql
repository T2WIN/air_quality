-- ============================================================
-- v_openaq_deduped: Deduplicated OpenAQ hourly data
-- Returns only the latest ingested_at row per dedup_key
-- ============================================================

CREATE OR REPLACE VIEW `{project_id}.{staging_dataset}.v_openaq_deduped` AS
SELECT
  * EXCEPT(rn)
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY dedup_key
      ORDER BY ingested_at DESC
    ) AS rn
  FROM `{project_id}.{raw_dataset}.openaq_hourly`
)
WHERE rn = 1;
