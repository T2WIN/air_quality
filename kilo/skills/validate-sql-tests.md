# validate-sql-tests

## Purpose

Run the BigQuery view test harness to verify that all warehouse views produce correct results against deterministic fixture data. Use this skill after modifying any view SQL, fixture data, raw schema, or assertion file.

---

## When to use

- After creating or modifying any view in `warehouse/staging/` or `warehouse/analytics/`
- After changing `warehouse/tests/fixtures/seed_core.sql`
- After changing `warehouse/tests/schemas/raw_tables.sql`
- After adding or editing any file in `warehouse/tests/assertions/`
- After changing `warehouse/tests/view_manifest.json`
- When asked to verify the warehouse layer works end-to-end

---

## Prerequisites

1. **Working directory must be the repository root.**
2. **`.env` must exist** at the repo root with these variables set:
   - `DEV_PROJECT_ID` (must be `air-quality-test-490920`)
   - `BQ_LOCATION` (must be `EU`)
   - `BQ_RAW_DATASET`
   - `BQ_STAGING_DATASET`
   - `BQ_ANALYTICS_DATASET`
3. **Google Cloud authentication** must be active:
   ```bash
   gcloud auth application-default login
   gcloud config set project air-quality-test-490920
   ```
4. **Python dependencies** installed: `google-cloud-bigquery`, `python-dotenv`, `pandas`, `pyarrow`

Run :
```bash
python3 -c "import google.cloud.bigquery; import dotenv; import pandas; import pyarrow" \
  || echo "Missing dependencies: pip install google-cloud-bigquery python-dotenv pandas pyarrow"
```
To verify. If not working, ask user for help.

---

## How to run

### Run the full suite (default)

```bash
python3 -m warehouse.tests.run_view_tests
```

This will:
1. Create three temporary datasets in BigQuery (raw, staging, analytics)
2. Create raw tables matching production schema
3. Seed deterministic fixture data
4. Create all 7 views in dependency order
5. Run all assertion SQL files
6. Print PASS/FAIL per test
7. Write a JSON report to `warehouse/tests/.reports/view_test_report.json`
8. Drop all temporary datasets
9. Exit 0 if all pass, exit 1 if any fail or error

### Run only tests matching a pattern

```bash
python3 -m warehouse.tests.run_view_tests --only v_openaq_deduped
python3 -m warehouse.tests.run_view_tests --only freshness
python3 -m warehouse.tests.run_view_tests --only ingestion_overview
```

### Stop on first failure

```bash
python3 -m warehouse.tests.run_view_tests --stop-on-first-failure
```

### Keep temp datasets on failure for manual inspection

```bash
python3 -m warehouse.tests.run_view_tests --keep-datasets-on-failure
```

When datasets are preserved, the runner prints the dataset names. Query them directly:

```sql
SELECT * FROM `air-quality-test-490920.air_quality_staging_test_20260322_abc1.v_openaq_deduped` LIMIT 10;
```

**You must manually drop preserved datasets when done:**

```bash
bq rm -r -f air-quality-test-490920:air_quality_raw_test_20260322_abc1
bq rm -r -f air-quality-test-490920:air_quality_staging_test_20260322_abc1
bq rm -r -f air-quality-test-490920:air_quality_analytics_test_20260322_abc1
```

### Custom report path

```bash
python3 -m warehouse.tests.run_view_tests --report-path /tmp/my_report.json
```

---

## How to read the output

### Console output structure

```text
[setup]   – project, location, temp dataset names
[views]   – PASS or FAIL per view compilation
[assert]  – PASS, FAIL, or ERROR per assertion
[result]  – final summary line
```

### Result statuses

| Status | Meaning |
|--------|---------|
| **PASS** | Assertion query returned 0 rows — logic is correct |
| **FAIL** | Assertion query returned 1+ rows — view logic is wrong |
| **ERROR** | Assertion query could not execute — bad SQL, missing table/column, unresolved placeholder |

### Example failure output

```text
[assert] FAIL v_station_current_outlook__aggregations_are_correct
         violating_rows=1
         sample={"entity_id":"station_a","expected_value":"avg_temp:12.0,...","actual_value":"avg_temp:15.0,...","reason":"Weather aggregations do not match expected"}
```

The `sample` line shows the first violating row with `expected_value`, `actual_value`, and `reason` — use these to identify what went wrong.

---

## How to read the JSON report

Path: `warehouse/tests/.reports/view_test_report.json`

```json
{
  "run_id": "20260322_143012_ab1c",
  "project_id": "air-quality-test-490920",
  "location": "EU",
  "datasets": {
    "raw": "air_quality_raw_test_20260322_143012_ab1c",
    "staging": "air_quality_staging_test_20260322_143012_ab1c",
    "analytics": "air_quality_analytics_test_20260322_143012_ab1c"
  },
  "status": "passed|failed",
  "summary": {
    "tests_total": 20,
    "passed": 20,
    "failed": 0,
    "errors": 0
  },
  "tests": [
    { "name": "v_openaq_deduped__latest_ingested_wins", "status": "passed" },
    {
      "name": "v_station_current_outlook__aggregations_are_correct",
      "status": "failed",
      "violations": [{ "entity_id": "station_a", "expected_value": "...", "actual_value": "...", "reason": "..." }]
    }
  ]
}
```

Check `status` first. If `"failed"`, scan the `tests` array for entries where `status` is `"failed"` or `"error"` and read their `violations` or `error_message`.

---

## Architecture overview

### File layout

```
warehouse/
├── staging/
│   ├── v_openaq_deduped.sql              # Staging view SQL
│   └── v_weather_deduped.sql
├── analytics/
│   ├── v_station_latest_pollutants.sql   # Analytics view SQL
│   ├── v_station_hourly_wide.sql
│   ├── v_station_current_outlook.sql
│   ├── v_station_freshness.sql
│   └── v_ingestion_overview.sql
└── tests/
    ├── run_view_tests.py                 # Test runner
    ├── view_manifest.json                # View dependency order + file paths
    ├── schemas/
    │   └── raw_tables.sql                # Temp table DDL
    ├── fixtures/
    │   └── seed_core.sql                 # Deterministic test data
    ├── assertions/
    │   ├── v_openaq_deduped__*.sql        # Assertion SQL files
    │   ├── v_weather_deduped__*.sql
    │   ├── v_station_latest_pollutants__*.sql
    │   ├── v_station_hourly_wide__*.sql
    │   ├── v_station_current_outlook__*.sql
    │   ├── v_station_freshness__*.sql
    │   └── v_ingestion_overview__*.sql
    └── .reports/
        └── view_test_report.json         # Generated report (gitignored)
```

### View creation order (mandatory)

Views are created in this order per `view_manifest.json`. The runner always stops if any view fails to compile because downstream views depend on upstream ones.

**Stage 1 — Staging:**
1. `v_openaq_deduped`
2. `v_weather_deduped`

**Stage 2 — Analytics:**
3. `v_station_latest_pollutants`
4. `v_station_hourly_wide`
5. `v_station_current_outlook`
6. `v_station_freshness`
7. `v_ingestion_overview`

### SQL templating

Every SQL file (views, schema, fixtures, assertions) uses these placeholders:

| Placeholder | Replaced with |
|---|---|
| `{project_id}` | `DEV_PROJECT_ID` from `.env` |
| `{raw_dataset}` | Temporary raw dataset name |
| `{staging_dataset}` | Temporary staging dataset name |
| `{analytics_dataset}` | Temporary analytics dataset name |
| `{reference_timestamp}` | `TIMESTAMP '2026-03-22 14:00:00 UTC'` |

**Rule:** Never hardcode dataset names in any SQL file. Always use placeholders.

### Assertion contract

Each assertion file is a single SQL query. The pass/fail rule:
- **0 rows returned → PASS**
- **1+ rows returned → FAIL**

Every assertion should return these columns on failure:
- `test_name` — identifies the test
- `entity_id` — the key of the failing row (station_id, source, etc.)
- `expected_value` — what the test expected
- `actual_value` — what the view actually produced
- `reason` — human/AI-readable explanation

---

## Fixture data reference

The fixture dataset is anchored at **reference time `2026-03-22 14:00:00 UTC`**.

### Stations

| station_id | Purpose | Has AQ data | Has weather |
|---|---|---|---|
| `station_a` | Full coverage, dedup tests, aggregation tests | Yes (all 3 pollutants, 4 hours) | Yes (4 forecasts, 1 duplicate) |
| `station_b` | Partial coverage, null handling | Yes (some missing pollutants) | No |
| `station_c` | Weather-only exclusion test | No | Yes |

### Key test scenarios embedded in fixtures

| Scenario | Where | What to look for |
|---|---|---|
| AQ dedup | `openaq_hourly` dedup_key `101_2026-03-22T10:00:00` | Two rows, value 22 (ingested 10:00) wins over 99 (ingested 09:00) |
| Weather dedup | `weather_forecasts` station_a valid_time 11:00 | Two rows, temp 10.0 (ingested 10:00) wins over -99.0 (ingested 09:00) |
| 3h window | `weather_forecasts` station_a | Hours 11/12/13 included, hour 14 excluded |
| Weather aggregation | station_a 3h window | avg_temp=12, total_precip=6, max_wind=7 |
| Null weather | station_b | No weather rows → NULL weather columns |
| Weather-only exclusion | station_c | Has weather but no AQ → must NOT appear in `v_station_current_outlook` |
| Missing pollutants | station_b hour 08:00 | Only pm25 → pm10 and no2 must be NULL |
| Freshness | station_a at ref time 14:00 | Latest data at 10:00 → 4h ago → `is_data_stale=TRUE`, `is_ingestion_stale=FALSE` |
| Ingestion 24h | openaq | 2 runs in window (run_1 success + run_error), run_old excluded |

---

## How to debug common failures

### FAIL: assertion returned violating rows

1. Read the `sample` in console output — it shows `expected_value` vs `actual_value`
2. Open the assertion SQL file and understand what it checks
3. Open the view SQL file being tested
4. Compare the view logic against the fixture data in `seed_core.sql`
5. If needed, re-run with `--keep-datasets-on-failure` and query the temp datasets directly

### ERROR: assertion query could not execute

Common causes:
- **Missing column**: view SQL doesn't produce a column the assertion expects → fix the view
- **Unresolved placeholder**: a `{something}` was not replaced → check for typos in placeholder names
- **Missing table/view**: an upstream view failed to compile → look at `[views]` output above the assertion errors
- **Syntax error**: bad SQL in the assertion file → fix the assertion

### All views fail with ERROR

Check:
- Is `.env` present and correct?
- Is `gcloud auth application-default login` current?
- Does the service account have BigQuery permissions on `DEV_PROJECT_ID`?

### Setup phase fails

- `setup__raw_tables` error → check `warehouse/tests/schemas/raw_tables.sql` for syntax issues
- `setup__seed_core` error → check `warehouse/tests/fixtures/seed_core.sql` for column mismatches vs schema

---

## How to add a new view

1. **Write the view SQL** in `warehouse/staging/` or `warehouse/analytics/` using placeholders
2. **Add an entry to `warehouse/tests/view_manifest.json`**:
   ```json
   {
     "name": "v_my_new_view",
     "file_path": "warehouse/analytics/v_my_new_view.sql",
     "stage": 2,
     "order": 6,
     "dependencies": ["v_openaq_deduped"],
     "dataset": "analytics",
     "description": "Description of what this view does"
   }
   ```
3. **Add fixture data** to `seed_core.sql` if the view needs data not already present
4. **Write assertion files** in `warehouse/tests/assertions/` named `v_my_new_view__<check>.sql`
5. **Run the suite** to verify

---

## How to add a new assertion

1. Create a file `warehouse/tests/assertions/<view_name>__<check_name>.sql`
2. Write a query that returns **0 rows on success** and **1+ rows on failure**
3. Include columns: `test_name`, `entity_id`, `expected_value`, `actual_value`, `reason`
4. Use placeholders for all dataset references
5. Match the internal `test_name` string to the filename stem exactly

### Assertion template

```sql
-- Assert: <description of what this checks>

WITH expected AS (
  SELECT '<key>' AS entity_id, '<expected>' AS expected_value
),
actual AS (
  SELECT
    <key_column> AS entity_id,
    <actual_column> AS actual_value
  FROM `{project_id}.{analytics_dataset}.<view_name>`
  WHERE <filter>
)
SELECT
  '<view_name>__<check_name>' AS test_name,
  e.entity_id,
  e.expected_value,
  COALESCE(a.actual_value, 'NULL') AS actual_value,
  '<explanation of what went wrong>' AS reason
FROM expected e
LEFT JOIN actual a ON e.entity_id = a.entity_id
WHERE a.entity_id IS NULL
   OR a.actual_value != e.expected_value;
```

---

## How to modify fixture data

1. Edit `warehouse/tests/fixtures/seed_core.sql`
2. **Check every assertion** that references the values you changed — update expected values accordingly
3. If adding a new table, also add its DDL to `warehouse/tests/schemas/raw_tables.sql`
4. Run the full suite to verify nothing broke

**Critical rule:** all timestamps must be fixed constants. Never use `CURRENT_TIMESTAMP()` or any dynamic time function in fixtures.

---

## How to modify the raw schema

1. Edit `warehouse/tests/schemas/raw_tables.sql`
2. Ensure column names match what the production schema uses
3. Update `seed_core.sql` if you added or renamed columns
4. Run the full suite

---

## Current assertion inventory

| View | Assertion file | What it checks |
|---|---|---|
| `v_openaq_deduped` | `__latest_ingested_wins` | Duplicate dedup_key → latest ingested_at row kept |
| `v_openaq_deduped` | `__no_duplicate_sensor_hour` | No duplicate (sensor_id, period_from_utc) pairs |
| `v_weather_deduped` | `__latest_ingested_wins` | Duplicate (station, valid_time) → latest ingested_at kept |
| `v_weather_deduped` | `__no_duplicate_station_valid_time` | No duplicate (station_id, valid_time) pairs |
| `v_station_latest_pollutants` | `__one_row_per_station` | Grain check |
| `v_station_latest_pollutants` | `__latest_values_are_correct` | pm25=22, pm10=30, no2=45 for station_a |
| `v_station_latest_pollutants` | `__metadata_present` | station_name, locality, etc. joined correctly |
| `v_station_hourly_wide` | `__one_row_per_station_hour` | Grain check |
| `v_station_hourly_wide` | `__pivot_is_correct` | station_a 10:00 → pm25=22, pm10=30, no2=45 |
| `v_station_hourly_wide` | `__missing_pollutants_are_null` | station_b 08:00 → pm10=NULL, no2=NULL |
| `v_station_current_outlook` | `__one_row_per_station` | Grain check |
| `v_station_current_outlook` | `__uses_next_3h_from_ingested_at` | forecast_hours_count=3 (excludes +4h) |
| `v_station_current_outlook` | `__aggregations_are_correct` | avg_temp=12, total_precip=6, max_wind=7 |
| `v_station_current_outlook` | `__null_weather_when_missing` | station_b → all weather columns NULL |
| `v_station_current_outlook` | `__requires_pollutant_presence` | station_c (weather-only) not in output |
| `v_station_freshness` | `__one_row_per_station` | Grain check |
| `v_station_freshness` | `__staleness_flags_are_correct` | station_a: data_stale=TRUE, ingestion_stale=FALSE |
| `v_ingestion_overview` | `__one_row_per_source` | Grain check |
| `v_ingestion_overview` | `__latest_run_is_correct` | Correct run_id, status, started_at per source |
| `v_ingestion_overview` | `__last_24h_aggregates_are_correct` | Correct run/error counts and record totals |

---

## Cleanup safety

- By default, temp datasets are **always dropped** at the end of the run, even on failure
- Use `--keep-datasets-on-failure` only for interactive debugging
- If a run is interrupted (Ctrl+C, crash), orphaned datasets may remain — find and drop them:
  ```bash
  bq ls --project_id=air-quality-test-490920 | grep _test_
  ```
