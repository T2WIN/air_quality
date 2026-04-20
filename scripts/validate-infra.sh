#!/usr/bin/env bash
# validate-infra.sh — infrastructure checks, with optional Cloud Run job test executions
# Usage:
#   bash scripts/validate-infra.sh <PROJECT_ID>
#   bash scripts/validate-infra.sh <PROJECT_ID> --run-jobs

set -u -o pipefail

PROJECT="${1:?Usage: validate-infra.sh <PROJECT_ID> [--run-jobs]}"
RUN_JOBS="${2:-}"

RUN_TEST_JOBS=false
if [[ "$RUN_JOBS" == "--run-jobs" ]]; then
  RUN_TEST_JOBS=true
fi

PASS=0
FAIL=0
SKIP=0

required_vars=(
  BQ_RAW_DATASET
  BQ_STAGING_DATASET
  BQ_ANALYTICS_DATASET
  BQ_OPENAQ_HOURLY_TABLE
  BQ_WEATHER_TABLE
  BQ_STATION_SENSORS_TABLE
  REGION
  OPENAQ_JOB_NAME
  OPENMETEO_JOB_NAME
  SCHEDULER_JOB_OPENAQ
  SCHEDULER_JOB_OPENMETEO
  OPENAQ_SERVICE_ACCOUNT_NAME
  OPENMETEO_SERVICE_ACCOUNT_NAME
  SCHEDULER_SERVICE_ACCOUNT_NAME
)

for v in "${required_vars[@]}"; do
  : "${!v:?Environment variable $v must be set}"
done

OPENAQ_SERVICE_ACCOUNT_EMAIL="${OPENAQ_SERVICE_ACCOUNT_NAME}@${PROJECT}.iam.gserviceaccount.com"
OPENMETEO_SERVICE_ACCOUNT_EMAIL="${OPENMETEO_SERVICE_ACCOUNT_NAME}@${PROJECT}.iam.gserviceaccount.com"
SCHEDULER_SERVICE_ACCOUNT_EMAIL="${SCHEDULER_SERVICE_ACCOUNT_NAME}@${PROJECT}.iam.gserviceaccount.com"

check() {
  local label="$1"
  local cmd="$2"

  if bash -o pipefail -c "$cmd" >/dev/null 2>&1; then
    echo "  ✓ $label"
    ((PASS++))
  else
    echo "  ✗ $label"
    ((FAIL++))
  fi
}

skip() {
  local label="$1"
  echo "  - $label (skipped)"
  ((SKIP++))
}

echo ""
echo "Validating project: $PROJECT"
if [[ "$RUN_TEST_JOBS" == true ]]; then
  echo "Mode: infra validation + test job executions"
else
  echo "Mode: read-only infra validation"
fi
echo ""

# -------- BigQuery Datasets --------
echo "=== BigQuery Datasets ==="
check "raw dataset"       "bq show '${PROJECT}:${BQ_RAW_DATASET}'"
check "staging dataset"   "bq show '${PROJECT}:${BQ_STAGING_DATASET}'"
check "analytics dataset" "bq show '${PROJECT}:${BQ_ANALYTICS_DATASET}'"

# -------- BigQuery Tables --------
echo "=== BigQuery Tables ==="
check "openaq table"          "bq show '${PROJECT}:${BQ_RAW_DATASET}.${BQ_OPENAQ_HOURLY_TABLE}'"
check "weather table"         "bq show '${PROJECT}:${BQ_RAW_DATASET}.${BQ_WEATHER_TABLE}'"
check "station_sensors table" "bq show '${PROJECT}:${BQ_RAW_DATASET}.${BQ_STATION_SENSORS_TABLE}'"

# -------- BigQuery Views --------
echo "=== BigQuery Views ==="
check "openaq staging view" \
  "bq show --format=prettyjson '${PROJECT}:${BQ_STAGING_DATASET}.v_openaq_deduped' | grep -q '\"type\": \"VIEW\"'"
  check "open-meteo staging view" \
  "bq show --format=prettyjson '${PROJECT}:${BQ_STAGING_DATASET}.v_weather_deduped' | grep -q '\"type\": \"VIEW\"'"

# -------- Cloud Run Jobs --------
echo "=== Cloud Run Jobs ==="
check "openaq job" \
  "gcloud run jobs describe '$OPENAQ_JOB_NAME' --region='$REGION' --project='$PROJECT' --format='value(name)'"
check "weather job" \
  "gcloud run jobs describe '$OPENMETEO_JOB_NAME' --region='$REGION' --project='$PROJECT' --format='value(name)'"

# -------- Cloud Scheduler --------
echo "=== Cloud Scheduler ==="
check "openaq scheduler" \
  "gcloud scheduler jobs describe '$SCHEDULER_JOB_OPENAQ' --location='$REGION' --project='$PROJECT' --format='value(name)'"
check "weather scheduler" \
  "gcloud scheduler jobs describe '$SCHEDULER_JOB_OPENMETEO' --location='$REGION' --project='$PROJECT' --format='value(name)'"

# -------- Service Accounts --------
echo "=== Service Accounts ==="
check "openaq SA" \
  "gcloud iam service-accounts describe '$OPENAQ_SERVICE_ACCOUNT_EMAIL' --project='$PROJECT'"
check "weather SA" \
  "gcloud iam service-accounts describe '$OPENMETEO_SERVICE_ACCOUNT_EMAIL' --project='$PROJECT'"
check "scheduler SA" \
  "gcloud iam service-accounts describe '$SCHEDULER_SERVICE_ACCOUNT_EMAIL' --project='$PROJECT'"

# -------- IAM Bindings --------
echo "=== IAM Bindings ==="
check "openaq SA has direct bigquery.jobUser binding" \
  "gcloud projects get-iam-policy '$PROJECT' \
    --flatten='bindings[].members' \
    --filter=\"bindings.role:roles/bigquery.jobUser AND bindings.members:serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}\" \
    --format='value(bindings.role)' | grep -Fxq 'roles/bigquery.jobUser'"

check "weather SA has direct bigquery.jobUser binding" \
  "gcloud projects get-iam-policy '$PROJECT' \
    --flatten='bindings[].members' \
    --filter=\"bindings.role:roles/bigquery.jobUser AND bindings.members:serviceAccount:${OPENMETEO_SERVICE_ACCOUNT_EMAIL}\" \
    --format='value(bindings.role)' | grep -Fxq 'roles/bigquery.jobUser'"

check "scheduler SA can execute openaq job" \
  "gcloud run jobs get-iam-policy '$OPENAQ_JOB_NAME' \
    --region='$REGION' \
    --project='$PROJECT' \
    --flatten='bindings[].members' \
    --filter=\"bindings.members:serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}\" \
    --format='value(bindings.role)' | grep -Eq '^(roles/run\\.jobsExecutor|roles/run\\.invoker)$'"

check "scheduler SA can execute weather job" \
  "gcloud run jobs get-iam-policy '$OPENMETEO_JOB_NAME' \
    --region='$REGION' \
    --project='$PROJECT' \
    --flatten='bindings[].members' \
    --filter=\"bindings.members:serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}\" \
    --format='value(bindings.role)' | grep -Eq '^(roles/run\\.jobsExecutor|roles/run\\.invoker)$'"

# -------- Test Run Cloud Run Jobs --------
echo "=== Cloud Run Job Test Executions ==="
if [[ "$RUN_TEST_JOBS" == true ]]; then
  check "execute openaq job successfully" \
    "gcloud run jobs execute '$OPENAQ_JOB_NAME' \
      --region='$REGION' \
      --project='$PROJECT' \
      --wait"

  check "execute weather job successfully" \
    "gcloud run jobs execute '$OPENMETEO_JOB_NAME' \
      --region='$REGION' \
      --project='$PROJECT' \
      --wait"
  check "openaq table has recent data" \
    "bq query --nouse_legacy_sql --format=csv \
    \"SELECT COUNT(*) > 0
        FROM \`${PROJECT}.${BQ_RAW_DATASET}.${BQ_OPENAQ_HOURLY_TABLE}\`
      WHERE ingested_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)\" \
    | tail -n 1 | grep -Fxq 'true'"
else
  skip "execute openaq job successfully"
  skip "execute weather job successfully"
fi

# -------- Summary --------
TOTAL=$((PASS + FAIL + SKIP))
echo ""
echo "=== Summary ==="
echo "  Passed:  $PASS"
echo "  Failed:  $FAIL"
echo "  Skipped: $SKIP"
echo "  Total:   $TOTAL"

if [[ $FAIL -gt 0 ]]; then
  echo "  ⚠ $FAIL check(s) FAILED — review above"
  exit 1
else
  echo "  ✅ All non-skipped checks passed"
  exit 0
fi