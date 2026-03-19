# Air Quality Platform – Project Setup Runbook

## Scope

This runbook deploys the full ingestion layer:

- BigQuery datasets, tables, and staging views
- Pub/Sub topic
- Secret Manager secrets
- Cloud Run service (OpenAQ) and Cloud Run job (Weather)
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

---

## 1 – Enable APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  bigquery.googleapis.com \
  pubsub.googleapis.com \
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

## 3 – BigQuery datasets

```bash
bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:${BQ_RAW_DATASET}"     || true
bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:${BQ_STAGING_DATASET}"  || true
```

---

## 4 – BigQuery raw tables

```bash
bq query \
  --location="$BQ_LOCATION" \
  --project_id="$PROJECT_ID" \
  --use_legacy_sql=false \
  < warehouse/raw/create_raw_tables.sql
```

---

## 5 – Secrets

Create these in the Secret Manager console (or via CLI):

| Secret name      | Description        |
|------------------|--------------------|
| `OPENAQ_API_KEY` | OpenAQ v3 API key  |

---

## 6 – Load static reference data

Reads `OPENAQ_API_KEY` from Secret Manager, writes the
`station_sensors` lookup table to BigQuery:

```bash
python ingestion/static/station_metadata.py
```

---

## 7 – BigQuery staging views

OpenAQ dedup view:

```bash
bq query \
  --location="$BQ_LOCATION" \
  --project_id="$PROJECT_ID" \
  --use_legacy_sql=false \
<<EOF
CREATE OR REPLACE VIEW \`${PROJECT_ID}.${BQ_STAGING_DATASET}.openaq_hourly_latest\` AS
SELECT * EXCEPT (rn)
FROM (
  SELECT *,
         ROW_NUMBER() OVER (
           PARTITION BY dedup_key
           ORDER BY ingested_at DESC
         ) AS rn
  FROM \`${PROJECT_ID}.${BQ_RAW_DATASET}.${BQ_OPENAQ_HOURLY_TABLE}\`
)
WHERE rn = 1;
EOF
```

Weather dedup view:

```bash
bq query \
  --location="$BQ_LOCATION" \
  --use_legacy_sql=false \
  < warehouse/staging/create_weather_views.sql
```

---

## 8 – Pub/Sub

```bash
gcloud pubsub topics create "$PUBSUB_TOPIC" || true

gcloud pubsub subscriptions create "$PUBSUB_SUBSCRIPTION" \
  --topic="$PUBSUB_TOPIC" \
  --expiration-period=7d \
  || true
```

---

## 9 – Service accounts

```bash
gcloud iam service-accounts create "$OPENAQ_SERVICE_ACCOUNT_NAME" \
  --display-name="OpenAQ poller runtime" || true

gcloud iam service-accounts create "$OPENMETEO_SERVICE_ACCOUNT_NAME" \
  --display-name="Weather poller runtime" || true

gcloud iam service-accounts create "$SCHEDULER_SERVICE_ACCOUNT_NAME" \
  --display-name="Cloud Scheduler invoker" || true
```

---

## 10 – IAM bindings

### 10a – Runtime permissions for pollers

Both pollers need `bigquery.jobUser` to run queries.
Table-level and Pub/Sub publish permissions are granted in the console
(dataset-level `bigquery.dataEditor` for now).
The OpenAQ poller also needs access to the `OPENAQ_API_KEY` secret
(granted in the console).

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.jobUser"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${OPENMETEO_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.jobUser"
```

### 10b – Scheduler → Cloud Run invocation

```bash
# Invoke the OpenAQ Cloud Run *service*
gcloud run services add-iam-policy-binding "$OPENAQ_SERVICE_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/run.invoker"

# Execute the Weather Cloud Run *job*
gcloud run jobs add-iam-policy-binding "$OPENMETEO_SERVICE_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/run.jobsExecutor"
```

### 10c – Token minting

Cloud Scheduler's Google-managed service agent must be allowed to
create OIDC/OAuth tokens for the scheduler service account.
Without this the scheduler silently fails to call its targets.

```bash
gcloud iam service-accounts add-iam-policy-binding "$SCHEDULER_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

### 10d – Act-as for Cloud Run job execution

When Scheduler triggers a Cloud Run job via the Admin API it must be
able to act as the job's runtime SA:

```bash
gcloud iam service-accounts add-iam-policy-binding "$OPENMETEO_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/iam.serviceAccountUser"
```

---

## 11 – Build & deploy the OpenAQ poller (Cloud Run service)

```bash
export IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
export OPENAQ_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/openaq-poller:${IMAGE_TAG}"

gcloud builds submit ingestion/openaq_poller --tag "$OPENAQ_IMAGE"

gcloud run deploy "$OPENAQ_SERVICE_NAME" \
  --image="$OPENAQ_IMAGE" \
  --region="$REGION" \
  --service-account="${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  --no-allow-unauthenticated \
  --memory=512Mi --cpu=1 --timeout=900 --max-instances=1 \
  --set-secrets="OPENAQ_API_KEY=OPENAQ_API_KEY:latest" \
  --set-env-vars="^@^BQ_LOCATION=${BQ_LOCATION}\
@BQ_RAW_DATASET=${BQ_RAW_DATASET}\
@BQ_STATION_SENSORS_TABLE=${BQ_STATION_SENSORS_TABLE}\
@BQ_OPENAQ_HOURLY_TABLE=${BQ_OPENAQ_HOURLY_TABLE}\
@OPENAQ_BASE_URL=${OPENAQ_BASE_URL}\
@PUBSUB_TOPIC=${PUBSUB_TOPIC}\
@LOOKBACK_HOURS=${LOOKBACK_HOURS}\
@MAX_WORKERS=${MAX_WORKERS}\
@HTTP_TIMEOUT_SECONDS=${HTTP_TIMEOUT_SECONDS}\
@ENFORCE_COMPLETE_HOURS=${ENFORCE_COMPLETE_HOURS}\
@DEV_STATION_IDS=${DEV_STATION_IDS}\
@MAX_SENSORS=${MAX_SENSORS}@"

export OPENAQ_URL=$(gcloud run services describe "$OPENAQ_SERVICE_NAME" \
  --region="$REGION" --format='value(status.url)')
echo "OpenAQ service URL: $OPENAQ_URL"
```

---

## 12 – Build & deploy the Weather poller (Cloud Run job)

```bash
export WEATHER_IMAGE="gcr.io/${PROJECT_ID}/weather-poller"

gcloud builds submit ingestion/weather_poller --tag "$WEATHER_IMAGE"

gcloud run jobs create "$OPENMETEO_SERVICE_NAME" \
  --image="$WEATHER_IMAGE" \
  --region="$REGION" \
  --service-account="${OPENMETEO_SERVICE_ACCOUNT_EMAIL}" \
  --memory=512Mi --cpu=1 --task-timeout=600s --max-retries=0 \
  --set-env-vars="\
PROJECT_ID=${PROJECT_ID},\
BQ_RAW_DATASET=${BQ_RAW_DATASET},\
BQ_WEATHER_TABLE=${BQ_WEATHER_TABLE},\
PUBSUB_TOPIC=${PUBSUB_TOPIC},\
BATCH_SIZE=${BATCH_SIZE},\
FORECAST_HOURS=${FORECAST_HOURS}"
```

To update an existing job, replace `create` with `update` using the
same flags.

Quick smoke test:

```bash
gcloud run jobs execute "$OPENMETEO_SERVICE_NAME" --region="$REGION" --wait
```

---

## 13 – Cloud Scheduler jobs

### OpenAQ (calls the Cloud Run service via HTTP)

```bash
gcloud scheduler jobs create http "$SCHEDULER_JOB_OPENAQ" \
  --location="$REGION" \
  --schedule="5 * * * *" \
  --http-method=POST \
  --uri="${OPENAQ_URL}/run" \
  --oidc-service-account-email="${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --oidc-token-audience="${OPENAQ_URL}" \
  || true
```

### Weather (calls the Cloud Run Jobs Admin API)

```bash
gcloud scheduler jobs create http "$SCHEDULER_JOB_OPENMETEO" \
  --location="$REGION" \
  --schedule="0 */6 * * *" \
  --time-zone="UTC" \
  --http-method=POST \
  --uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${OPENMETEO_SERVICE_NAME}:run" \
  --oauth-service-account-email="${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
  --message-body="{}" \
  || true
```

To change a schedule later use `gcloud scheduler jobs update http …`
with the same flags.

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

# Check weather job execution
gcloud run jobs executions list --job="$OPENMETEO_SERVICE_NAME" \
  --region="$REGION" --limit=3

# Spot-check BigQuery
bq query --use_legacy_sql=false \
  "SELECT COUNT(*) AS n FROM \`${PROJECT_ID}.${BQ_RAW_DATASET}.${BQ_OPENAQ_HOURLY_TABLE}\`"
bq query --use_legacy_sql=false \
  "SELECT COUNT(*) AS n FROM \`${PROJECT_ID}.${BQ_RAW_DATASET}.${BQ_WEATHER_TABLE}\`"
```
```

---

### What changed from the original

| Problem in the original | Fix |
|---|---|
| Duplicate / out-of-order step numbers (two "14"s, jump from 4→12) | Renumbered sequentially 0–14 |
| IAM section never granted `serviceAccountTokenCreator` to the Scheduler service agent — root cause of the silent failures | Added **step 10c** |
| Missing `iam.serviceAccountUser` for weather job execution | Added **step 10d** |
| `$PROJECT_NUMBER` never derived | Derived in step 0 |
| Weather poller build used `gcr.io` but OpenAQ used Artifact Registry — inconsistent | Kept both as-is but grouped clearly; easy to unify later |
| No verification section | Added **step 14** with force-run + BigQuery checks |
| Secrets step interleaved after tables for no reason | Moved secrets before any script that reads them |
| `set-env-vars` one-liner was hard to read/diff | Split across lines with backslash continuation |
| `|| true` missing on idempotent creates (Pub/Sub, Scheduler) | Added so the script is fully re-runnable |