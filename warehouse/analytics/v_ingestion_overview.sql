-- ============================================================
-- v_ingestion_overview: Cross-system ingestion status
-- Shows latest run and 24h aggregates per source
-- Uses ${REFERENCE_TIMESTAMP} for testability
-- ============================================================

CREATE OR REPLACE VIEW `${PROJECT_ID}.${BQ_ANALYTICS_DATASET}.v_ingestion_overview` AS
WITH latest_runs AS (
  -- Get the latest run per source
  SELECT
    source,
    run_id AS latest_run_id,
    status AS latest_run_status,
    run_started_at AS latest_run_started_at,
    run_finished_at AS latest_run_finished_at,
    duration_seconds AS latest_run_duration_seconds,
    records_written AS latest_run_records_written,
    error_message AS latest_run_error_message,
    ROW_NUMBER() OVER (
      PARTITION BY source
      ORDER BY run_started_at DESC
    ) AS rn
  FROM `${PROJECT_ID}.${BQ_RAW_DATASET}.ingestion_log`
),
aggregates_24h AS (
  -- Aggregate metrics over last 24h from reference timestamp
  -- Boundary rule: runs > 24h old are excluded (runs exactly 24h old are NOT counted)
  -- This is a strict "within the last 24 hours" interpretation
  SELECT
    source,
    COUNT(*) AS runs_24h,
    COUNTIF(status = 'success') AS successes_24h,
    COUNTIF(status = 'error') AS errors_24h,
    COUNTIF(status = 'partial_success') AS partials_24h,
    SUM(records_written) AS total_records_24h,
    SUM(api_calls) AS total_api_calls_24h,
    SUM(api_errors) AS total_api_errors_24h
  FROM `${PROJECT_ID}.${BQ_RAW_DATASET}.ingestion_log`
  WHERE run_started_at > TIMESTAMP_SUB(${REFERENCE_TIMESTAMP}, INTERVAL 24 HOUR)
  GROUP BY source
)
SELECT
  lr.source,
  -- Latest run details
  lr.latest_run_id,
  lr.latest_run_status,
  lr.latest_run_started_at,
  lr.latest_run_finished_at,
  lr.latest_run_duration_seconds,
  lr.latest_run_records_written,
  lr.latest_run_error_message,
  -- 24h aggregates
  COALESCE(a.runs_24h, 0) AS runs_24h,
  COALESCE(a.successes_24h, 0) AS successes_24h,
  COALESCE(a.errors_24h, 0) AS errors_24h,
  COALESCE(a.partials_24h, 0) AS partials_24h,
  COALESCE(a.total_records_24h, 0) AS total_records_24h,
  COALESCE(a.total_api_calls_24h, 0) AS total_api_calls_24h,
  COALESCE(a.total_api_errors_24h, 0) AS total_api_errors_24h
FROM latest_runs AS lr
LEFT JOIN aggregates_24h AS a
  ON lr.source = a.source
WHERE lr.rn = 1;
