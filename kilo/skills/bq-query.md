# Run SQL Against BigQuery

## When to use
- Deploying views or tables (dev project only for writes)
- Running validation queries
- Investigating data (read-only in prod)

## Prerequisites
When reading the prod
```bash
set -a && source .env && set +a
```

When using the dev project
```bash
set -a && source .env_test && set +a
```

## Running a SQL file
```bash
bq query \
  --location="$BQ_LOCATION" \
  --project_id="$DEV_PROJECT_ID" \
  --use_legacy_sql=false \
  < path/to/file.sql
```

## Running inline SQL
```bash
bq query \
  --location="$BQ_LOCATION" \
  --project_id="$DEV_PROJECT_ID" \
  --use_legacy_sql=false \
  "SELECT COUNT(*) FROM \`${DEV_PROJECT_ID}.${BQ_RAW_DATASET}.${BQ_OPENAQ_HOURLY_TABLE}\`"
```

## Rules
- **Writes (CREATE, INSERT, DELETE)**: dev project only. Use `$DEV_PROJECT_ID`.
- **Reads (SELECT)**: dev or prod. Use `$DEV_PROJECT_ID` or `$PROJECT_ID`.
- Always use `--use_legacy_sql=false`.
- Always specify `--location` and `--project_id`.