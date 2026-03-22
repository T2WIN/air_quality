# Validate Infrastructure State

## When to use
After running the runbook or making infrastructure changes.

Use this to verify that expected infrastructure resources exist and are configured correctly.

By default, the validation is read-only.  
Optionally, you can also run Cloud Run jobs as a smoke test.

## Prerequisites
```bash
set -a && source .env && set +a
export TARGET_PROJECT="${DEV_PROJECT_ID}"
```

## Steps

### 1. Read-only infrastructure validation
Run the validation script:

```bash
bash scripts/validate-infra.sh "$TARGET_PROJECT"
```

This checks:

- BigQuery datasets
- BigQuery tables
- BigQuery views
- Cloud Run jobs
- Cloud Scheduler jobs
- Service accounts
- IAM bindings

### 2. Optional smoke test: execute the jobs
To validate that the deployed jobs can actually run, execute:

```bash
bash scripts/validate-infra.sh "$TARGET_PROJECT" --run-jobs
```

This does all of the read-only checks above, and also:

- executes the OpenAQ Cloud Run job (should last around 15min)
- executes the weather Cloud Run job (should be around 3min)
- waits for both executions to complete

## Success criteria

### Read-only mode
All required checks print `✓`.

The job execution checks will appear as skipped.

### Smoke test mode
All required checks print `✓`, including the job execution checks.

Any `✗` means the infrastructure or configuration is incomplete or incorrect.

## Notes
- Running without `--run-jobs` does not modify infrastructure.
- Running with `--run-jobs` is not read-only: it may call external APIs, write data, and incur runtime cost.
```

