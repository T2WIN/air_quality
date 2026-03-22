# Validate SQL Views with Assertion Queries

## When to use
After creating or modifying BigQuery views. Runs the assertion queries
defined in `warehouse/tests/`.

## Prerequisites
- View must be deployed to the target project
- Assertion queries must exist in `warehouse/tests/test_<view_name>.sql`

## Steps

### 1. Find the test file
```bash
ls warehouse/tests/test_*.sql
```

### 2. Run each assertion query
Each assertion query in the test file should return 0 rows if the view is correct.

```bash
bq query \
  --location="$BQ_LOCATION" \
  --project_id="$DEV_PROJECT_ID" \
  --use_legacy_sql=false \
  --format=json \
  < warehouse/tests/test_<view_name>.sql
```

If a test file contains multiple queries separated by `-- ASSERT:` comments,
run each one individually.

### 3. Evaluate results
- **0 rows returned**: PASS
- **Any rows returned**: FAIL — the rows ARE the violations. Report them.

## Test file format convention
```sql
-- ASSERT: No duplicate dedup_keys
SELECT dedup_key, COUNT(*) AS cnt
FROM `{project}.{dataset}.{view}`
GROUP BY dedup_key
HAVING cnt > 1;

-- ASSERT: No nulls in identity columns
SELECT *
FROM `{project}.{dataset}.{view}`
WHERE station_id IS NULL
   OR period_from_utc IS NULL;
```

## If tests fail
1. Report which assertion failed and the violating rows.
2. Investigate the view SQL for the root cause.
3. Fix the view, redeploy, and re-run the tests.