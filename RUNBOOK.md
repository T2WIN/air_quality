# Air Quality Platform – Project Setup Runbook

## Scope

This runbook deploys the full ingestion layer:

- BigQuery datasets, tables, and staging views
- Secret Manager secrets
- Cloud Run Job (OpenAQ, Weather)
- Cloud Scheduler triggers for both
- IAM bindings (least-privilege)

**Assumptions:**

| Setting          | Value               |
|------------------|---------------------|
| Project          | `air-quality-490517`|
| Region           | `europe-west1`      |
| BigQuery location| `EU`                |

All commands use variables from `.env`. Load them once at the top and
every value propagates automatically.

---

## 0 – Prerequisites

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project air-quality-490517
```

Load environment variables (run this in every new shell):

```bash
set -a && source .env && set +a
```

Derive the project number (needed for IAM later):

```bash
export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" \
  --format='value(projectNumber)')
```

## 0a – Derive Service Account emails

SA emails are derived from the active `PROJECT_ID` so the same runbook works
against dev and production:

```bash
export OPENAQ_SERVICE_ACCOUNT_EMAIL="${OPENAQ_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export OPENMETEO_SERVICE_ACCOUNT_EMAIL="${OPENMETEO_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export SCHEDULER_SERVICE_ACCOUNT_EMAIL="${SCHEDULER_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL="${DASHBOARD_READER_SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
```

---

## 1 – Enable APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  bigquery.googleapis.com \
  cloudscheduler.googleapis.com \
  iam.googleapis.com \
  secretmanager.googleapis.com
```

---

## 2 – Artifact Registry

```bash
gcloud artifacts repositories create "$REPO_NAME" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Docker images for air quality services" \
  || true
```

---

## 3 – BigQuery datasets and views

```bash
bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:${BQ_RAW_DATASET}"     || true
bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:${BQ_STAGING_DATASET}"  || true
bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:${BQ_ANALYTICS_DATASET}"  || true

```

---

## 4 – BigQuery raw tables

```bash
bq query \
  --location="$BQ_LOCATION" \
  --project_id="$PROJECT_ID" \
  --use_legacy_sql=false \
  < warehouse/raw/create_raw_tables.sql

bq query \
  --location="$BQ_LOCATION" \
  --project_id="$PROJECT_ID" \
  --use_legacy_sql=false \
  < warehouse/raw/create_ingestion_log_table.sql
```

---

## 5 – Secrets

Create these in the Secret Manager console (or via CLI):

| Secret name      | Description        |
|------------------|--------------------|
| `OPENAQ_API_KEY` | OpenAQ v3 API key  |

---

## 6 – Load static reference data

Ask the user to create the API key and store it in Secret Manager.

Reads `OPENAQ_API_KEY` from Secret Manager, writes the
`station_sensors` lookup table to BigQuery:

```bash
pip install google-cloud-bigquery google-cloud-secret-manager requests
python ingestion/static/station_metadata.py
```

---

## 7 – BigQuery views

```bash
envsubst < warehouse/staging/create_dedup_views.sql | bq query \
  --location="$BQ_LOCATION" \
  --project_id="$PROJECT_ID" \
  --use_legacy_sql=false
envsubst < warehouse/staging/create_ingestion_freshness_view.sql | bq query \
  --location="$BQ_LOCATION" \
  --project_id="$PROJECT_ID" \
  --use_legacy_sql=false
envsubst < warehouse/staging/create_station_freshness_view.sql | bq query \
  --location="$BQ_LOCATION" \
  --project_id="$PROJECT_ID" \
  --use_legacy_sql=false
```

---

---

## 9 – Service accounts

```bash
gcloud iam service-accounts create "$OPENAQ_SERVICE_ACCOUNT_NAME" \
  --display-name="OpenAQ poller runtime" || true

gcloud iam service-accounts create "$OPENMETEO_SERVICE_ACCOUNT_NAME" \
  --display-name="Weather poller runtime" || true

gcloud iam service-accounts create "$SCHEDULER_SERVICE_ACCOUNT_NAME" \
  --display-name="Cloud Scheduler invoker" || true

gcloud iam service-accounts create "${DASHBOARD_READER_SERVICE_ACCOUNT_NAME}" \
  --project="${PROJECT_ID}" \
  --display-name="Air Quality Dashboard (read-only BQ)" \
  --description="Used by Streamlit dashboard. Read-only access to air_quality datasets."
```

---

## 10 – IAM bindings

### 10a – Runtime permissions for pollers

Both pollers need three BigQuery roles:
- `bigquery.dataViewer` – read reference tables (e.g. `station_sensors`)
- `bigquery.dataEditor` – write data to raw tables and create ingestion log
- `bigquery.jobUser` – run BigQuery load/export jobs

The OpenAQ poller also needs access to the `OPENAQ_API_KEY` secret
(granted via Cloud Run Job env vars).

```bash
# Data reading (station_sensors lookup)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.dataViewer"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${OPENMETEO_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.dataViewer"

# Data writing (load to raw tables + ingestion_log)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${OPENMETEO_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.dataEditor"

# Job execution (query/load jobs)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.jobUser"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${OPENMETEO_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.jobUser"
```


### 10d – Act-as for Cloud Run job execution

When Scheduler triggers a Cloud Run job via the Admin API it must be
able to act as the job's runtime SA:

```bash
gcloud iam service-accounts add-iam-policy-binding "$OPENMETEO_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/iam.serviceAccountUser"


gcloud iam service-accounts add-iam-policy-binding "$OPENAQ_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/iam.serviceAccountUser"
```

### 10e – Dashboard permissions

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.dataViewer"
```

```bash
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.jobUser" \
  --condition=None
```

---

## 11 – Build pollers (Cloud Run Job)

```bash
export IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
export OPENAQ_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/openaq-poller:${IMAGE_TAG}"
export WEATHER_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/weather-poller:${IMAGE_TAG}"

gcloud builds submit . \
  --config=cloudbuild.yaml \
  --substitutions="_TAG=${IMAGE_TAG},_REGION=${REGION},_REPO=${REPO_NAME}"

gcloud run jobs create "$OPENAQ_JOB_NAME" \
  --image="$OPENAQ_IMAGE" \
  --region="$REGION" \
  --service-account="${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  --tasks=1 \
  --parallelism=1 \
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
  || true

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
  || true
```

---

## 12 – Scheduler IAM for Cloud Run Execution

```bash
gcloud run jobs add-iam-policy-binding "$OPENAQ_JOB_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/run.jobsExecutor"

gcloud run jobs add-iam-policy-binding "$OPENMETEO_JOB_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/run.jobsExecutor"
```


## 13 – Cloud Scheduler jobs

### OpenAQ (calls the Cloud Run Jobs Admin API)

```bash
gcloud scheduler jobs update http "$SCHEDULER_JOB_OPENAQ" \
  --location="$REGION" \
  --schedule="5 */2 * * *" \
  --time-zone="UTC" \
  --http-method=POST \
  --uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${OPENAQ_JOB_NAME}:run" \
  --oauth-service-account-email="${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
  --message-body="{}" \
  || true
```

### Weather (calls the Cloud Run Jobs Admin API)

```bash
gcloud scheduler jobs create http "$SCHEDULER_JOB_OPENMETEO" \
  --location="$REGION" \
  --schedule="0 */6 * * *" \
  --time-zone="UTC" \
  --http-method=POST \
  --uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${OPENMETEO_JOB_NAME}:run" \
  --oauth-service-account-email="${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
  --message-body="{}" \
  || true
```

To change a schedule later use `gcloud scheduler jobs update http …`
with the same flags.

---

## 13a – Scheduler token minting

Cloud Scheduler's Google-managed service agent (`service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com`)
must be allowed to create OIDC/OAuth tokens for the scheduler service account.
This step runs **after** the scheduler jobs are created so the Google-managed SA exists.

```bash
gcloud iam service-accounts add-iam-policy-binding "$SCHEDULER_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

---

## 14 – Verify end-to-end

Force-trigger both schedulers and confirm rows land in BigQuery:

```bash
gcloud scheduler jobs run "$SCHEDULER_JOB_OPENAQ"   --location="$REGION"
gcloud scheduler jobs run "$SCHEDULER_JOB_OPENMETEO" --location="$REGION"

# Check scheduler status
gcloud scheduler jobs describe "$SCHEDULER_JOB_OPENAQ"   --location="$REGION" \
  --format="yaml(lastAttemptTime,status)"
gcloud scheduler jobs describe "$SCHEDULER_JOB_OPENMETEO" --location="$REGION" \
  --format="yaml(lastAttemptTime,status)"
```

## 15 – Alerts on Job failures

List available notification channels:

```bash
gcloud beta monitoring channels list
```

Create a notification channel (if needed):

```bash
gcloud beta monitoring channels create \
  --display-name="Team Email" \
  --type=email \
  --channel-labels=email_address=grandclaye49@gmail.com
```

Create the alert policy:

```bash
gcloud monitoring policies create \
  --display-name="Cloud Run Job Failure Alert" \
  --condition-display-name="Job execution failed" \
  --condition-filter='resource.type = "cloud_run_job" AND metric.type = "run.googleapis.com/job/completed_execution_count" AND metric.labels.result = "FAILED"' \
  --aggregation='{"alignmentPeriod":"60s","perSeriesAligner":"ALIGN_COUNT"}' \
  --duration="0s" \
  --if="> 0" \
  --combiner="OR" \
  --notification-channels="$ALERT_CHANNEL_ID" \
  --documentation="Alert triggered when a Cloud Run Job execution fails."
```

## 16 – Deploy the Streamlit dashboard
Create views :
```bash
bq query \
  --location="$BQ_LOCATION" \
  --project_id="$PROJECT_ID" \
  --use_legacy_sql=false \
  < warehouse/viz/create_analytics_views.sql
```

```bash

export IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
export DASHBOARD_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/dashboard:${IMAGE_TAG}"

gcloud builds submit dashboard --tag "${DASHBOARD_IMAGE}"
gcloud run deploy air-quality-dashboard \
  --project=${PROJECT_ID} \
  --image=${DASHBOARD_IMAGE} \
  --region=${REGION} \
  --service-account=${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL} \
  --allow-unauthenticated \
  --memory=512Mi \
  --cpu=1 \
  --max-instances=3
```

---