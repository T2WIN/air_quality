# Tear Down Dev Environment

## When to use
Before running the full runbook to ensure a clean-slate test.

## Prerequisites
- `DEV_PROJECT_ID` must be set in `.env`
- User must confirm before execution

## Rules
- **NEVER run against production.** Verify the project before proceeding:
```bash
echo "Target project: $DEV_PROJECT_ID"
# Confirm this is NOT air-quality-490517
```

## Steps

### 1. Delete Cloud Scheduler jobs
```bash
gcloud scheduler jobs delete "$SCHEDULER_JOB_OPENAQ" --location="$REGION" --project="$DEV_PROJECT_ID" --quiet || true
gcloud scheduler jobs delete "$SCHEDULER_JOB_OPENMETEO" --location="$REGION" --project="$DEV_PROJECT_ID" --quiet || true
```

### 2. Delete Cloud Run jobs
```bash
gcloud run jobs delete "$OPENAQ_JOB_NAME" --region="$REGION" --project="$DEV_PROJECT_ID" --quiet || true
gcloud run jobs delete "$OPENMETEO_JOB_NAME" --region="$REGION" --project="$DEV_PROJECT_ID" --quiet || true
```

### 3. Delete Dashboard service
```bash
gcloud run services delete "$DASHBOARD_SERVICE_NAME" --region="$REGION" --project="$DEV_PROJECT_ID" --quiet || true
```

### 4. Delete BigQuery datasets (cascading)
```bash
bq rm -r -f --project_id="$DEV_PROJECT_ID" "${DEV_PROJECT_ID}:${BQ_RAW_DATASET}" || true
bq rm -r -f --project_id="$DEV_PROJECT_ID" "${DEV_PROJECT_ID}:${BQ_STAGING_DATASET}" || true
bq rm -r -f --project_id="$DEV_PROJECT_ID" "${DEV_PROJECT_ID}:${BQ_ANALYTICS_DATASET}" || true
```

### 5. Delete service accounts

#### 5.1. Remove IAM bindings
Remove all bindings BEFORE deleting the service accounts. This prevents
orphaned deleted:serviceAccount:… entries in the project IAM policy.
Derive the project number (needed for the Cloud Scheduler service agent binding):

```bash
export DEV_PROJECT_NUMBER=$(gcloud projects describe "$DEV_PROJECT_ID" \
  --format='value(projectNumber)')

gcloud projects remove-iam-policy-binding "$DEV_PROJECT_ID" \
  --member="serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.jobUser" --quiet || true

gcloud projects remove-iam-policy-binding "$DEV_PROJECT_ID" \
  --member="serviceAccount:${OPENMETEO_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.jobUser" --quiet || true

gcloud projects remove-iam-policy-binding "$DEV_PROJECT_ID" \
  --member="serviceAccount:${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.dataViewer" --quiet || true

gcloud projects remove-iam-policy-binding "$DEV_PROJECT_ID" \
  --member="serviceAccount:${DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/bigquery.jobUser" --quiet || true

gcloud iam service-accounts remove-iam-policy-binding "$SCHEDULER_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:service-${DEV_PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project="$DEV_PROJECT_ID" --quiet || true

gcloud iam service-accounts remove-iam-policy-binding "$OPENAQ_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/iam.serviceAccountUser" \
  --project="$DEV_PROJECT_ID" --quiet || true

gcloud iam service-accounts remove-iam-policy-binding "$OPENMETEO_SERVICE_ACCOUNT_EMAIL" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/iam.serviceAccountUser" \
  --project="$DEV_PROJECT_ID" --quiet || true

gcloud secrets remove-iam-policy-binding OPENAQ_API_KEY \
  --member="serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --project="$DEV_PROJECT_ID" --quiet || true

```

```bash
gcloud iam service-accounts delete "$OPENAQ_SERVICE_ACCOUNT_EMAIL" --project="$DEV_PROJECT_ID" --quiet || true
gcloud iam service-accounts delete "$OPENMETEO_SERVICE_ACCOUNT_EMAIL" --project="$DEV_PROJECT_ID" --quiet || true
gcloud iam service-accounts delete "$SCHEDULER_SERVICE_ACCOUNT_EMAIL" --project="$DEV_PROJECT_ID" --quiet || true
gcloud iam service-accounts delete "$DASHBOARD_READER_SERVICE_ACCOUNT_EMAIL" --project="$DEV_PROJECT_ID" --quiet || true
```

### 6. Delete Artifact Registry repository
```bash
gcloud artifacts repositories delete "$REPO_NAME" --location="$REGION" --project="$DEV_PROJECT_ID" --quiet || true
```

## Success criteria
All commands complete (exit 0 or "not found" errors caught by `|| true`).
Running `validate-infra` afterwards should show all resources as FAIL/missing.




Good catch again. The runbook creates `OPENAQ_API_KEY` in Secret Manager (§5), and the OpenAQ poller SA has a secret accessor binding on it (noted as "granted in console" in §10a). Both need to be cleaned up.

Here are the additions — I'll show exactly where they slot into the existing teardown:

### Add to Step 6 — new section 6e (after 6d, before step 7)

```markdown
#### 6e. Secret-level bindings — secret accessor (RUNBOOK §10a, noted as console-granted)
The OpenAQ poller SA has `secretmanager.secretAccessor` on the API key secret.
Remove before deleting the SA and the secret.

```bash
gcloud secrets remove-iam-policy-binding OPENAQ_API_KEY \
  --member="serviceAccount:${OPENAQ_SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --project="$DEV_PROJECT_ID" --quiet || true
```