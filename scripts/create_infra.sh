#!/bin/bash
# Air Quality Platform - Automated Setup Script
# Usage: Ensure .env is populated, then run: bash setup.sh

set -euo pipefail

# ------------------------------------------------------------------
# 0 – Prerequisites & Environment
# ------------------------------------------------------------------
echo "Step 0: Loading environment and setting prerequisites..."

if [[ ! -f .env ]]; then
  echo "ERROR: .env file not found. Please create it first."
  exit 1
fi

set -a && source .env && set +a

# Select environment from command argument (defaults to prod)
ENV_TARGET="${1:-prod}"

if [[ "$ENV_TARGET" == "dev" ]]; then
  export PROJECT_ID="$DEV_PROJECT_ID"
  echo ">>> Targeting DEV environment: $PROJECT_ID"
elif [[ "$ENV_TARGET" == "prod" ]]; then
  echo ">>> Targeting PROD environment: $PROJECT_ID"
else
  echo "ERROR: Invalid argument. Usage: bash setup.sh [dev|prod]"
  exit 1
fi

gcloud config set project "$PROJECT_ID" --quiet

export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)' --quiet)

export OPENAQ_SERVICE_ACCOUNT_EMAIL="${OPENAQ_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export OPENMETEO_SERVICE_ACCOUNT_EMAIL="${OPENMETEO_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export SCHEDULER_SERVICE_ACCOUNT_EMAIL="${SCHEDULER_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL="${DASHBOARD_READER_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# ------------------------------------------------------------------
# 1 – Enable APIs
# ------------------------------------------------------------------
echo "Step 1: Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  bigquery.googleapis.com \
  cloudscheduler.googleapis.com \
  iam.googleapis.com \
  secretmanager.googleapis.com \
  --quiet

# ------------------------------------------------------------------
# 2 – Artifact Registry
# ------------------------------------------------------------------
echo "Step 2: Creating Artifact Registry..."
gcloud artifacts repositories create "$REPO_NAME" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Docker images for air quality services" \
  --quiet || true

# ------------------------------------------------------------------
# 3 – BigQuery datasets
# ------------------------------------------------------------------
echo "Step 3: Creating BigQuery datasets..."
bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:${BQ_RAW_DATASET}" || true
bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:${BQ_STAGING_DATASET}" || true
bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:${BQ_ANALYTICS_DATASET}" || true

# ------------------------------------------------------------------
# 4 – BigQuery raw tables
# ------------------------------------------------------------------
echo "Step 4: Creating BigQuery raw tables..."
envsubst < warehouse/raw/create_raw_tables.sql | bq query --location="$BQ_LOCATION" --project_id="$PROJECT_ID" --use_legacy_sql=false 
envsubst < warehouse/raw/create_ingestion_log_table.sql | bq query --location="$BQ_LOCATION" --project_id="$PROJECT_ID" --use_legacy_sql=false 

# ------------------------------------------------------------------
# 5 & 6 – Secrets & Static Reference Data
# ------------------------------------------------------------------
echo "Step 5 & 6: Checking for OPENAQ_API_KEY secret..."

if ! gcloud secrets describe OPENAQ_API_KEY --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
  echo "======================================================================"
  echo "ACTION REQUIRED: OPENAQ_API_KEY secret is missing."
  echo ""
  echo "Please create it manually using one of these methods:"
  echo ""
  echo "  1. Via CLI (run this in a separate terminal):"
  echo "     echo -n 'YOUR_ACTUAL_API_KEY' | gcloud secrets create OPENAQ_API_KEY --data-file=-"
  echo ""
  echo "  2. Via GCP Console:"
  echo "     https://console.cloud.google.com/security/secret-manager?project=${PROJECT_ID}"
  echo "     Click 'Create Secret', name it 'OPENAQ_API_KEY', and paste your key."
  echo ""
  echo "Once the secret exists, re-run this script."
  echo "======================================================================"
  exit 1
fi

echo " -> Secret found. Loading static reference data..."
pip install -q google-cloud-bigquery google-cloud-secret-manager requests
python3 ingestion/static/station_metadata.py

# ------------------------------------------------------------------
# 7 – Staging views
# ------------------------------------------------------------------
echo "Step 7: Creating staging views..."
envsubst < warehouse/staging/v_openaq_deduped.sql | bq query --location="$BQ_LOCATION" --project_id="$PROJECT_ID" --use_legacy_sql=false
envsubst < warehouse/staging/v_weather_deduped.sql | bq query --location="$BQ_LOCATION" --project_id="$PROJECT_ID" --use_legacy_sql=false

# ------------------------------------------------------------------
# 8 – Analytics views
# ------------------------------------------------------------------
echo "Step 8: Creating analytics views..."
export REFERENCE_TIMESTAMP="CURRENT_TIMESTAMP()"

envsubst < warehouse/analytics/v_ingestion_overview.sql | bq query --location="$BQ_LOCATION" --project_id="$PROJECT_ID" --use_legacy_sql=false
envsubst < warehouse/analytics/v_station_current_outlook.sql | bq query --location="$BQ_LOCATION" --project_id="$PROJECT_ID" --use_legacy_sql=false
envsubst < warehouse/analytics/v_station_freshness.sql | bq query --location="$BQ_LOCATION" --project_id="$PROJECT_ID" --use_legacy_sql=false
envsubst < warehouse/analytics/v_station_hourly_wide.sql | bq query --location="$BQ_LOCATION" --project_id="$PROJECT_ID" --use_legacy_sql=false
envsubst < warehouse/analytics/v_station_latest_pollutants.sql | bq query --location="$BQ_LOCATION" --project_id="$PROJECT_ID" --use_legacy_sql=false

# ------------------------------------------------------------------
# 9 – Service accounts
# ------------------------------------------------------------------
echo "Step 9: Creating service accounts..."
gcloud iam service-accounts create "$OPENAQ_SERVICE_ACCOUNT_NAME" --display-name="OpenAQ poller runtime" --quiet || true
gcloud iam service-accounts create "$OPENMETEO_SERVICE_ACCOUNT_NAME" --display-name="Weather poller runtime" --quiet || true
gcloud iam service-accounts create "$SCHEDULER_SERVICE_ACCOUNT_NAME" --display-name="Cloud Scheduler invoker" --quiet || true
gcloud iam service-accounts create "${DASHBOARD_READER_SERVICE_ACCOUNT_NAME}" \
  --project="${PROJECT_ID}" \
  --display-name="Air Quality Dashboard (read-only BQ)" \
  --description="Used by Streamlit dashboard. Read-only access to air_quality datasets." \
  --quiet || true

sleep 30

# ------------------------------------------------------------------
# 10 – IAM bindings
# ------------------------------------------------------------------
echo "Step 10: Configuring IAM bindings..."

# 10a - Runtime permissions
for SA_EMAIL in "$OPENAQ_SERVICE_ACCOUNT_EMAIL" "$OPENMETEO_SERVICE_ACCOUNT_EMAIL"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${SA_EMAIL}" --role="roles/bigquery.dataViewer" --quiet
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${SA_EMAIL}" --role="roles/bigquery.dataEditor" --quiet
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${SA_EMAIL}" --role="roles/bigquery.jobUser" --quiet
done
gcloud secrets add-iam-policy-binding OPENAQ_API_KEY \
    --member="serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$DEV_PROJECT_ID" --quiet

# 10d - Act-as for Cloud Run job execution
gcloud iam service-accounts add-iam-policy-binding "$OPENMETEO_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" --role="roles/iam.serviceAccountUser" --quiet
gcloud iam service-accounts add-iam-policy-binding "$OPENAQ_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" --role="roles/iam.serviceAccountUser" --quiet

# 10e - Dashboard permissions
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL}" --role="roles/bigquery.dataViewer" --quiet
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL}" --role="roles/bigquery.jobUser" --condition=None --quiet

sleep 30
# ------------------------------------------------------------------
# 11 – Build pollers (Cloud Run Job)
# ------------------------------------------------------------------
echo "Step 11: Building and deploying Cloud Run Jobs..."
export IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
export OPENAQ_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/openaq-poller:${IMAGE_TAG}"
export WEATHER_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/weather-poller:${IMAGE_TAG}"

gcloud builds submit . \
  --config=cloudbuild.yaml \
  --substitutions="_TAG=${IMAGE_TAG},_REGION=${REGION},_REPO=${REPO_NAME}" \
  --quiet

gcloud run jobs create "$OPENAQ_JOB_NAME" \
  --image="$OPENAQ_IMAGE" \
  --region="$REGION" \
  --service-account="${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  --tasks=1 --parallelism=1 \
  --memory=512Mi --cpu=1 --task-timeout=1500s --max-retries=0 \
  --set-secrets="OPENAQ_API_KEY=OPENAQ_API_KEY:latest" \
  --set-env-vars="^@^BQ_LOCATION=${BQ_LOCATION}\
@PROJECT_ID=${PROJECT_ID}\
@BQ_RAW_DATASET=${BQ_RAW_DATASET}\
@BQ_STATION_SENSORS_TABLE=${BQ_STATION_SENSORS_TABLE}\
@BQ_OPENAQ_HOURLY_TABLE=${BQ_OPENAQ_HOURLY_TABLE}\
@OPENAQ_BASE_URL=${OPENAQ_BASE_URL}\
@LOOKBACK_HOURS=${LOOKBACK_HOURS}\
@MAX_WORKERS=${MAX_WORKERS}\
@HTTP_TIMEOUT_SECONDS=${HTTP_TIMEOUT_SECONDS}\
@ENFORCE_COMPLETE_HOURS=${ENFORCE_COMPLETE_HOURS}\
@DEV_STATION_IDS=${DEV_STATION_IDS}\
@TARGET_POLLUTANTS=${TARGET_POLLUTANTS}\
@OPENAQ_RATE_LIMIT_PER_MINUTE=${OPENAQ_RATE_LIMIT_PER_MINUTE}\
@OPENAQ_RATE_LIMIT_PER_HOUR=${OPENAQ_RATE_LIMIT_PER_HOUR}\
@MAX_HTTP_ATTEMPTS=${MAX_HTTP_ATTEMPTS}\
@PROGRESS_LOG_EVERY=${PROGRESS_LOG_EVERY}\
@PROGRESS_LOG_INTERVAL_SECONDS=${PROGRESS_LOG_INTERVAL_SECONDS}@" \
  --quiet || true

gcloud run jobs create "$OPENMETEO_JOB_NAME" \
  --image="$WEATHER_IMAGE" \
  --region="$REGION" \
  --service-account="${OPENMETEO_SERVICE_ACCOUNT_EMAIL}" \
  --memory=512Mi --cpu=1 --task-timeout=600s --max-retries=0 \
  --set-env-vars="\
PROJECT_ID=${PROJECT_ID},\
BQ_RAW_DATASET=${BQ_RAW_DATASET},\
BQ_WEATHER_TABLE=${BQ_WEATHER_TABLE},\
BATCH_SIZE=${BATCH_SIZE},\
FORECAST_HOURS=${FORECAST_HOURS}" \
  --quiet || true

sleep 60
# ------------------------------------------------------------------
# 12 – Scheduler IAM for Cloud Run Execution and Token Minting
# ------------------------------------------------------------------
echo "Step 12: Configuring Scheduler IAM..."
gcloud run jobs add-iam-policy-binding "$OPENAQ_JOB_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/run.jobsExecutor" \
  --quiet

gcloud run jobs add-iam-policy-binding "$OPENMETEO_JOB_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/run.jobsExecutor" \
  --quiet

gcloud iam service-accounts add-iam-policy-binding "$SCHEDULER_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" --quiet


sleep 60
# ------------------------------------------------------------------
# 13 – Cloud Scheduler jobs
# ------------------------------------------------------------------
echo "Step 13: Setting up Cloud Scheduler jobs..."
gcloud scheduler jobs delete "$SCHEDULER_JOB_OPENAQ" --location="$REGION" --quiet 2>/dev/null || true

gcloud scheduler jobs create http "$SCHEDULER_JOB_OPENAQ" \
  --location="$REGION" \
  --schedule="5 */2 * * *" \
  --time-zone="UTC" \
  --http-method=POST \
  --uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${OPENAQ_JOB_NAME}:run" \
  --oauth-service-account-email="${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
  --message-body="{}" \
  --quiet

gcloud scheduler jobs delete "$SCHEDULER_JOB_OPENMETEO" --location="$REGION" --quiet 2>/dev/null || true

gcloud scheduler jobs create http "$SCHEDULER_JOB_OPENMETEO" \
  --location="$REGION" \
  --schedule="0 */6 * * *" \
  --time-zone="UTC" \
  --http-method=POST \
  --uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${OPENMETEO_JOB_NAME}:run" \
  --oauth-service-account-email="${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
  --message-body="{}" \
  --quiet || true

sleep 60
# ------------------------------------------------------------------
# 14 – Verify end-to-end
# ------------------------------------------------------------------
echo "Step 14: Force-triggering schedulers for verification..."
gcloud scheduler jobs run "$SCHEDULER_JOB_OPENAQ" --location="$REGION" --quiet || true
gcloud scheduler jobs run "$SCHEDULER_JOB_OPENMETEO" --location="$REGION" --quiet || true

echo "Scheduler Status:"
gcloud scheduler jobs describe "$SCHEDULER_JOB_OPENAQ" --location="$REGION" --format="yaml(lastAttemptTime,status)" 2>/dev/null || true
gcloud scheduler jobs describe "$SCHEDULER_JOB_OPENMETEO" --location="$REGION" --format="yaml(lastAttemptTime,status)" 2>/dev/null || true

echo "Setup completed successfully."