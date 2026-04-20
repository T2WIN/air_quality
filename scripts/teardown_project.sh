#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Tear Down Dev Environment
#
# Destroys all infrastructure resources in the DEV project so a full deploy
# can be tested against a clean slate.
#
# Prerequisites:
#   - .env file present with DEV_PROJECT_ID (and all other vars) defined
#   - gcloud, bq CLIs installed and authenticated
# =============================================================================

# --- Logging helpers --------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

ERROR_COUNT=0
WARN_COUNT=0
STEP_NUMBER=0

log()   { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${YELLOW}[WARN]${NC}  $*"; WARN_COUNT=$((WARN_COUNT + 1)); }
err()   { echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${RED}[ERROR]${NC} $*"; ERROR_COUNT=$((ERROR_COUNT + 1)); }

step() {
  STEP_NUMBER=$((STEP_NUMBER + 1))
  echo ""
  echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${BLUE}━━━ STEP ${STEP_NUMBER}: $* ━━━${NC}"
}

# Runs a command, logging outcome. Always succeeds (mirrors `|| true` intent).
# Usage: run_safe "description" <command> [args...]
run_safe() {
  local desc="$1"; shift
  log "$desc"
  if "$@" >/dev/null 2>&1; then
    ok "$desc — deleted"
  else
    warn "$desc — not found or already absent (ignored)"
  fi
}

# --- Safety checks ----------------------------------------------------------

PROD_PROJECT_ID="air-quality-490517"

set -a && source .env && set +a

if [ -z "${DEV_PROJECT_ID:-}" ]; then
  err "DEV_PROJECT_ID is not set. Check your .env file."
  exit 1
fi

log "Target project: $DEV_PROJECT_ID"

if [ "$DEV_PROJECT_ID" = "$PROD_PROJECT_ID" ]; then
  err "ABORTING: DEV_PROJECT_ID matches the production project ($PROD_PROJECT_ID)."
  err "NEVER run this script against production."
  exit 1
fi

ok "Confirmed target is NOT production ($PROD_PROJECT_ID)"

# --- Confirmation ------------------------------------------------------------

if [ -z "${AUTO_YES:-}" ]; then
  echo ""
  read -rp "This will destroy all dev resources in $DEV_PROJECT_ID. Continue? [y/N] " confirm
  if [[ "$confirm" != [yY] && "$confirm" != [yY][eE][sS] ]]; then
    log "Aborted by user."
    exit 0
  fi
else
  log "AUTO_YES is set — skipping confirmation prompt."
fi

# --- Derived values ----------------------------------------------------------

export DEV_PROJECT_NUMBER
DEV_PROJECT_NUMBER=$(gcloud projects describe "$DEV_PROJECT_ID" --format='value(projectNumber)') \
  || { err "Failed to look up project number for $DEV_PROJECT_ID"; exit 1; }
ok "Project number: $DEV_PROJECT_NUMBER"

export OPENAQ_SERVICE_ACCOUNT_EMAIL="${OPENAQ_SERVICE_ACCOUNT_NAME}@${DEV_PROJECT_ID}.iam.gserviceaccount.com"
export OPENMETEO_SERVICE_ACCOUNT_EMAIL="${OPENMETEO_SERVICE_ACCOUNT_NAME}@${DEV_PROJECT_ID}.iam.gserviceaccount.com"
export DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL="${DASHBOARD_READER_SERVICE_ACCOUNT_NAME}@${DEV_PROJECT_ID}.iam.gserviceaccount.com"
export SCHEDULER_SERVICE_ACCOUNT_EMAIL="${SCHEDULER_SERVICE_ACCOUNT_NAME}@${DEV_PROJECT_ID}.iam.gserviceaccount.com"

# --- Step 1: Cloud Scheduler jobs -------------------------------------------

step "Delete Cloud Scheduler jobs"

run_safe "Scheduler job: $SCHEDULER_JOB_OPENAQ" \
  gcloud scheduler jobs delete "$SCHEDULER_JOB_OPENAQ" --location="$REGION" --project="$DEV_PROJECT_ID" --quiet

run_safe "Scheduler job: $SCHEDULER_JOB_OPENMETEO" \
  gcloud scheduler jobs delete "$SCHEDULER_JOB_OPENMETEO" --location="$REGION" --project="$DEV_PROJECT_ID" --quiet

# --- Step 2: Cloud Run jobs -------------------------------------------------

step "Delete Cloud Run jobs"

run_safe "Cloud Run job: $OPENAQ_JOB_NAME" \
  gcloud run jobs delete "$OPENAQ_JOB_NAME" --region="$REGION" --project="$DEV_PROJECT_ID" --quiet

run_safe "Cloud Run job: $OPENMETEO_JOB_NAME" \
  gcloud run jobs delete "$OPENMETEO_JOB_NAME" --region="$REGION" --project="$DEV_PROJECT_ID" --quiet

# --- Step 3: Dashboard service ----------------------------------------------

step "Delete Dashboard Cloud Run service"

run_safe "Cloud Run service: $DASHBOARD_SERVICE_NAME" \
  gcloud run services delete "$DASHBOARD_SERVICE_NAME" --region="$REGION" --project="$DEV_PROJECT_ID" --quiet

# --- Step 4: BigQuery datasets ----------------------------------------------

step "Delete BigQuery datasets (cascading)"

run_safe "BQ dataset: ${BQ_RAW_DATASET}" \
  bq rm -r -f --project_id="$DEV_PROJECT_ID" "${DEV_PROJECT_ID}:${BQ_RAW_DATASET}"

run_safe "BQ dataset: ${BQ_STAGING_DATASET}" \
  bq rm -r -f --project_id="$DEV_PROJECT_ID" "${DEV_PROJECT_ID}:${BQ_STAGING_DATASET}"

run_safe "BQ dataset: ${BQ_ANALYTICS_DATASET}" \
  bq rm -r -f --project_id="$DEV_PROJECT_ID" "${DEV_PROJECT_ID}:${BQ_ANALYTICS_DATASET}"

# --- Step 5: Service accounts -----------------------------------------------

step "Remove IAM bindings (before deleting service accounts)"

run_safe "IAM: ${OPENAQ_SERVICE_ACCOUNT_EMAIL} → roles/bigquery.jobUser" \
  gcloud projects remove-iam-policy-binding "$DEV_PROJECT_ID" \
    --member="serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/bigquery.jobUser" --quiet

run_safe "IAM: ${OPENMETEO_SERVICE_ACCOUNT_EMAIL} → roles/bigquery.jobUser" \
  gcloud projects remove-iam-policy-binding "$DEV_PROJECT_ID" \
    --member="serviceAccount:${OPENMETEO_SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/bigquery.jobUser" --quiet

run_safe "IAM: ${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL} → roles/bigquery.dataViewer" \
  gcloud projects remove-iam-policy-binding "$DEV_PROJECT_ID" \
    --member="serviceAccount:${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/bigquery.dataViewer" --quiet

run_safe "IAM: ${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL} → roles/bigquery.jobUser" \
  gcloud projects remove-iam-policy-binding "$DEV_PROJECT_ID" \
    --member="serviceAccount:${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/bigquery.jobUser" --quiet

run_safe "IAM: Cloud Scheduler SA → tokenCreator on ${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  gcloud iam service-accounts remove-iam-policy-binding "$SCHEDULER_SERVICE_ACCOUNT_EMAIL" \
    --member="serviceAccount:service-${DEV_PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --project="$DEV_PROJECT_ID" --quiet

run_safe "IAM: ${SCHEDULER_SERVICE_ACCOUNT_EMAIL} → user on ${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  gcloud iam service-accounts remove-iam-policy-binding "$OPENAQ_SERVICE_ACCOUNT_EMAIL" \
    --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/iam.serviceAccountUser" \
    --project="$DEV_PROJECT_ID" --quiet

run_safe "IAM: ${SCHEDULER_SERVICE_ACCOUNT_EMAIL} → user on ${OPENMETEO_SERVICE_ACCOUNT_EMAIL}" \
  gcloud iam service-accounts remove-iam-policy-binding "$OPENMETEO_SERVICE_ACCOUNT_EMAIL" \
    --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/iam.serviceAccountUser" \
    --project="$DEV_PROJECT_ID" --quiet

run_safe "Secret IAM: ${OPENAQ_SERVICE_ACCOUNT_EMAIL} → accessor on OPENAQ_API_KEY" \
  gcloud secrets remove-iam-policy-binding OPENAQ_API_KEY \
    --member="serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$DEV_PROJECT_ID" --quiet

step "Delete service accounts"

run_safe "Service account: ${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  gcloud iam service-accounts delete "$OPENAQ_SERVICE_ACCOUNT_EMAIL" --project="$DEV_PROJECT_ID" --quiet

run_safe "Service account: ${OPENMETEO_SERVICE_ACCOUNT_EMAIL}" \
  gcloud iam service-accounts delete "$OPENMETEO_SERVICE_ACCOUNT_EMAIL" --project="$DEV_PROJECT_ID" --quiet

run_safe "Service account: ${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  gcloud iam service-accounts delete "$SCHEDULER_SERVICE_ACCOUNT_EMAIL" --project="$DEV_PROJECT_ID" --quiet

run_safe "Service account: ${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL}" \
  gcloud iam service-accounts delete "$DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL" --project="$DEV_PROJECT_ID" --quiet

# --- Step 6: Artifact Registry ----------------------------------------------

step "Delete Artifact Registry repository"

run_safe "Artifact repo: $REPO_NAME" \
  gcloud artifacts repositories delete "$REPO_NAME" --location="$REGION" --project="$DEV_PROJECT_ID" --quiet

# --- Summary ----------------------------------------------------------------

echo ""
echo -e "$(date '+%Y-%m-%d %H:%M:%S') ${BLUE}━━━ SUMMARY ━━━${NC}"
echo -e "  Steps executed : $STEP_NUMBER"
echo -e "  Warnings       : $WARN_COUNT"
echo -e "  Errors         : $ERROR_COUNT"
echo ""

if [ "$ERROR_COUNT" -gt 0 ]; then
  err "Teardown completed with errors. Review the log above."
  exit 1
elif [ "$WARN_COUNT" -gt 0 ]; then
  ok "Teardown completed successfully ($WARN_COUNT resource(s) were already absent)."
  ok "Run 'validate-infra' next — all resources should show FAIL/missing."
  exit 0
else
  ok "Teardown completed cleanly — all resources deleted."
  ok "Run 'validate-infra' next — all resources should show FAIL/missing."
  exit 0
fi