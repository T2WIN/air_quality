# AGENTS.md

## Project overview

Air quality data pipeline on GCP. Ingests OpenAQ (pollutant measurements) and Open-Meteo (weather forecasts) via Cloud Run Jobs on BigQuery, with a Streamlit dashboard. Targeting French monitoring stations.

## Architecture

```
ingestion/
  openaq_poller/    # Cloud Run Job — fetches hourly pollutant data from OpenAQ v3
  weather_poller/   # Cloud Run Job — fetches weather forecasts from Open-Meteo
  cams_poller/      # (empty placeholder — future)
  firms_poller/     # (empty placeholder — future)
  shared/           # rate_limiter, progress_tracker, ingestion_log, datetime_utils, http_utils
  static/           # station_metadata.py — one-off script to seed BQ lookup table
warehouse/
  raw/              # DDL for raw BigQuery tables
  staging/          # Dedup views (v_openaq_deduped, v_weather_deduped) — use `${PROJECT_ID}` / `${BQ_RAW_DATASET}` / `${BQ_STAGING_DATASET}` placeholders
  analytics/        # Analytics views — also templated with `${BQ_ANALYTICS_DATASET}` and `${REFERENCE_TIMESTAMP}`
  tests/            # BQ view test runner (creates temp datasets, seeds fixtures, runs SQL assertions)
dashboard/          # Streamlit app deployed as Cloud Run service
scripts/            # validate-infra.sh — GCP infra health check
```

## Key conventions

- **Config pattern**: Each poller uses a frozen `@dataclass` with `Config.from_env()`. Never scatter `os.getenv` calls outside `from_env()`.
- **Poller structure**: Both pollers follow the same shape — `run_poller(config, bq_client) -> PollerSummary`, status values: `success | partial_success | empty | error`. New pollers should mirror this.
- **Entry point**: `main() -> int`, exits with `raise SystemExit(main())`. Exit 0 on success/empty, 1 otherwise.
- **SQL DDL in `.sql` files only**: No DDL in Python. SELECT queries for data reads are OK inline.
- **SQL view placeholders**: Staging/analytics views use `${PROJECT_ID}`, `${BQ_RAW_DATASET}`, `${BQ_STAGING_DATASET}`, `${BQ_ANALYTICS_DATASET}`, `${REFERENCE_TIMESTAMP}` — rendered by `envsubst` at deploy or by the test runner's `render_sql_template`.
- **Idempotency**: Every pipeline step must be safe to re-run. Raw tables use `dedup_key` + staging views dedup via `ROW_NUMBER()`. DDL uses `CREATE TABLE IF NOT EXISTS` / `CREATE OR REPLACE VIEW`.

## Commands

### Local dev setup
```bash
set -a && source .env && set +a
```

### Run a poller locally
```bash
python -m ingestion.openaq_poller.main
python -m ingestion.weather_poller.main
```
Requires `PROJECT_ID` and GCP application-default credentials. Other env vars fall back to defaults in `Config.from_env()`.

### Run warehouse view tests
```bash
cd warehouse/tests && python run_view_tests.py
```
Requires `DEV_PROJECT_ID`, `BQ_LOCATION`, `BQ_RAW_DATASET`, `BQ_STAGING_DATASET`, `BQ_ANALYTICS_DATASET` env vars. Creates temporary BQ datasets, seeds fixtures, creates views, runs SQL assertions, then cleans up. Use `--keep-datasets-on-failure` to preserve datasets for debugging.

### Validate GCP infrastructure
```bash
bash scripts/validate-infra.sh $PROJECT_ID
bash scripts/validate-infra.sh $PROJECT_ID --run-jobs  # also triggers test job executions
```

### Deploy (from RUNBOOK.md)
```bash
# Build & submit both poller images
gcloud builds submit . --config=cloudbuild.yaml \
  --substitutions="_TAG=${IMAGE_TAG},_REGION=${REGION},_REPO=${REPO_NAME}"

# Deploy views (envsubst required for placeholder substitution)
envsubst < warehouse/staging/v_openaq_deduped.sql | bq query --use_legacy_sql=false
```

## Style and quality tools

- **Formatter**: Black (line length 100)
- **Import sort**: isort (profile `black`)
- **Linter**: Ruff (`ruff check`)
- **Type checker**: mypy (`--strict`)
- **SQL lint**: sqlfluff (`warehouse/`)
- Full CI check sequence: `black --check` → `isort --check` → `ruff check` → `mypy --strict` → `pytest tests/unit/` → `pytest --cov --cov-fail-under=80`

See `CODEBASE_QUALITY.md` for the complete style guide.

## Gotchas

- **Docker context**: Dockerfiles build from `ingestion/` as context. `COPY shared/` and `COPY __init__.py` references are relative to that directory.
- **Dockerfile discrepancy**: The two poller Dockerfiles differ in how they lay out the package — `openaq_poller/Dockerfile` copies into `/app/` with `CMD python -m ingestion.openaq_poller.main`, while `weather_poller/Dockerfile` explicitly creates the `ingestion/` package structure. The weather_poller pattern is the correct one.
- **No CI pipeline configured**: No `.github/` or CI workflows exist yet. The check sequence above is aspirational (documented in `CODEBASE_QUALITY.md`).
- **.env is gitignored**: Copy `.env.example` and fill in real values. `OPENAQ_API_KEY` default in code is a placeholder — production uses Secret Manager.
- ** cams_poller and firms_poller are empty stubs** — only Dockerfiles and requirements.txt exist, no code.
- **Dashboard uses Streamlit**: Not a standard web service. Runs on port 8080 with `--server.headless=true`. Deployed as Cloud Run with `--allow-unauthenticated`.
