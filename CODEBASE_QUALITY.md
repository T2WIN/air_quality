# Air Quality Platform — Code Quality Guidelines

> **Audience:** Every contributor to the Air Quality Platform repository.
> **Scope:** Python application code, SQL warehouse definitions, infrastructure scripts, and configuration.
> **Authority:** Treat these rules as mandatory for all new code and all modified code ("boy-scout rule"). Existing violations are tracked in *Appendix A* and resolved incrementally.

---

## 1 — General Principles

| # | Principle | Rationale |
|---|-----------|-----------|
| G1 | **Readability over cleverness.** Code is read far more than it is written. Optimise for the next reader. | Long-lived data pipelines are maintained by people who did not write them. |
| G2 | **Explicit over implicit.** Prefer visible configuration, named constants, and typed signatures over hidden defaults and magic values. | Pipeline bugs caused by invisible defaults are the hardest to diagnose. |
| G3 | **Fail loudly, recover gracefully.** Every error must be logged with enough context to diagnose. Transient errors should be retried; permanent errors should halt the component clearly. | Silent failures in scheduled jobs can go unnoticed for hours. |
| G4 | **Idempotency by default.** Every pipeline step must be safe to re-run without duplicating data or corrupting state. | Cloud Scheduler and Cloud Run can trigger duplicate executions. |
| G5 | **Least privilege.** Services, service accounts, and queries request only the permissions and data they need. | Follows GCP security best practices; reduces blast radius. |

---

## 2 — Python Code Standards

## 2.0 Components that do similar things should have similar code

For example, all pollers should have the same code structure, use similar patterns.

### 2.1 — Style and Formatting

| Rule | Detail |
|------|--------|
| **Formatter** | All Python files must be formatted with **Black** (line length 100). No exceptions. |
| **Import sorting** | Use **isort** (profile `black`). Group: stdlib → third-party → local. |
| **Linter** | **Ruff** (or flake8 + flake8-bugbear) must pass with zero warnings before merge. |
| **Line length** | Hard limit of 100 characters (Black default). SQL strings and URLs may exceed this inside dedented blocks but must remain readable. |

### 2.2 — Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Modules / files | `snake_case` | `rate_limiter.py` |
| Classes | `PascalCase` | `ProgressTracker` |
| Functions / methods | `snake_case`, verb-first | `fetch_batch()`, `load_station_sensors()` |
| Constants | `UPPER_SNAKE_CASE` | `RETRYABLE_STATUS_CODES` |
| Private helpers | Leading underscore | `_parse_retry_after()` |
| Variables | `snake_case`, descriptive | `window_start`, `sensors_queried` |
| Boolean variables/params | Prefix with `is_`, `has_`, `should_`, `enforce_` | `is_mobile`, `enforce_complete_hours` |
| Environment variables | `UPPER_SNAKE_CASE` | `BQ_RAW_DATASET` |

**Naming anti-patterns to avoid:**
- Single-letter names outside of tight comprehensions or loop indices.
- Abbreviations that are not universally understood (`ab`, `cfg` is borderline — `config` is preferred).
- Names that shadow builtins (`type`, `id`, `input`, `format`).

### 2.3 — Type Hints

- All function signatures **must** carry complete type annotations (parameters and return type).
- Use `from __future__ import annotations` at the top of every module to enable modern syntax (`list[str]` instead of `List[str]`).
- Complex structures should be modelled as `TypedDict`, `dataclass`, or `NamedTuple` — not bare `dict`.
- Run **mypy** (strict mode) in CI. Type: ignore comments require a justifying inline comment.

```python
# Good
def get_query_window(now: datetime, lookback_hours: int) -> tuple[datetime, datetime]: ...

# Bad
def get_query_window(now, lookback_hours): ...
```

### 2.4 — Configuration Management

| Rule | Detail |
|------|--------|
| **Single config object per service** | Every deployable service must read all its settings through a single frozen `dataclass` (e.g., `Config`). No module-level globals sourced from `os.environ` scattered across the file. |
| **No global mutable state for config** | Values like API keys must not be stored in mutable module-level variables. Pass them via the config object or function arguments. |
| **Parse once, pass everywhere** | `Config.from_env()` is called once in `main()`. All downstream functions receive `config` as a parameter — they never call `os.getenv` themselves. |
| **Validate eagerly** | `Config.from_env()` must raise immediately if a required variable is missing or malformed. Use clear error messages naming the variable. |
| **Document every variable** | Each env var must appear in `.env.example` (checked in) with a comment explaining its purpose, type, and default. |
| **No duplicate definitions** | Each variable must appear exactly once in `.env`. |

```python
# Good — frozen dataclass, parsed once
@dataclass(frozen=True)
class Config:
    project_id: str
    raw_dataset: str
    ...

    @classmethod
    def from_env(cls) -> Config:
        project_id = os.environ["PROJECT_ID"]  # fail fast if missing
        ...
```

### 2.5 — Error Handling

| Rule | Detail |
|------|--------|
| **Never swallow exceptions silently** | Every `except` block must log the exception with `logging.exception()` or `logging.error(..., exc_info=True)`. |
| **Use specific exception types** | Catch the narrowest exception possible. Bare `except Exception` is acceptable only at top-level orchestrators where you must guarantee a log row is written before exit. |
| **Distinguish transient vs. permanent** | Transient errors (network timeouts, 429/5xx) → retry with backoff. Permanent errors (401, 404, schema mismatch) → fail immediately with a clear message. |
| **Classify run outcomes** | Every poller run must end with a status of `success`, `partial_success`, `empty`, or `error`. The status logic must be defined in exactly one place. |
| **Structured failure context** | When recording per-item failures (sensors, stations), capture: item ID, error type, error message. Store as a JSON array in the ingestion log. |
| **Bubble up exit codes** | `main()` must return `0` on success and `1` on failure. Cloud Run uses the exit code to report job status. |

```python
# Good — explicit exit code, guaranteed log write
def main() -> int:
    try:
        config = Config.from_env()
        summary = run_poller(config)
        return 0 if summary["status"] == "success" else 1
    except Exception:
        logging.exception("Fatal error")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
```

### 2.6 — Logging

| Rule | Detail |
|------|--------|
| **Use the `logging` module exclusively** | Never use `print()` for operational output. `print()` is acceptable only in one-off local scripts and must be flagged for conversion before the script is promoted to production. |
| **Structured key-value pairs** | Log messages should use `key=value` pairs for machine-parseable context: `logging.info("Fetched batch  batch=%d  rows=%d", batch_num, len(rows))`. |
| **Log level semantics** | `DEBUG` = diagnostic detail; `INFO` = normal progress milestones; `WARNING` = recoverable anomaly; `ERROR` = failure requiring attention; `CRITICAL` = service cannot continue. |
| **Include identifiers** | Every log line during a run should be traceable to a `run_id`. Sensor/station-level logs should include the relevant ID. |
| **No sensitive data** | Never log API keys, secret values, or PII. Log secret *names* only (e.g., `"Loaded secret OPENAQ_API_KEY"`). |

### 2.7 — Function and Module Design

| Rule | Detail |
|------|--------|
| **Single responsibility** | Each function does one thing. If a function needs a comment block separating "phases," it should be split. |
| **Max function length** | Aim for ≤ 40 lines (excluding docstring). Functions over 60 lines must be justified in review. |
| **Pure functions where possible** | Functions that transform data should not perform I/O. Separate fetch → transform → load into distinct functions. |
| **Dependency injection over global singletons** | Pass clients (`bigquery.Client`, `requests.Session`) as parameters or via the config object. Thread-local singletons are acceptable for HTTP sessions in thread pools, but document the pattern. |
| **Docstrings** | Every public function and class must have a docstring. Use imperative mood for the first line. Describe parameters, return values, and raised exceptions for non-trivial functions. |

```python
def fetch_batch(
    latitudes: list[float],
    longitudes: list[float],
    *,
    base_url: str,
    timeout: int,
) -> list[dict]:
    """Fetch hourly weather forecasts for a batch of coordinates.

    Args:
        latitudes: Decimal latitudes of the stations.
        longitudes: Decimal longitudes of the stations.
        base_url: Open-Meteo API base URL.
        timeout: HTTP timeout in seconds.

    Returns:
        One dict per location with hourly forecast arrays.

    Raises:
        requests.HTTPError: On non-retryable HTTP failures.
    """
```

### 2.8 — Concurrency

| Rule | Detail |
|------|--------|
| **Protect shared state** | Any counter, list, or set modified from multiple threads must be guarded by a `threading.Lock`. |
| **Limit thread pool size** | Thread pool size must be configurable and default to a conservative value. Never create unbounded pools. |
| **Rate limiting is mandatory for external APIs** | Every external HTTP call must go through a rate limiter. Limits must be configurable via env vars. |
| **Graceful shutdown** | Background threads (heartbeat, progress) must be stoppable. Use `threading.Event` for signaling; join with a timeout. |

### 2.9 — Dependencies

| Rule | Detail |
|------|--------|
| **Pin all dependencies** | Every service must have a `requirements.txt` (pinned with `==`) with locked versions. |
| **Minimal dependency surface** | Do not add a library for something achievable in ≤ 20 lines of stdlib code. Every new dependency is a maintenance and security liability. |
| **Separate prod and dev dependencies** | Dev tools (pytest, mypy, black, ruff) go in `requirements-dev.txt` or a `[dev]` extra. They must not be installed in production containers. |

---

## 3 — SQL Standards (BigQuery)

### 3.1 — Formatting

| Rule | Detail |
|------|--------|
| **Uppercase keywords** | `SELECT`, `FROM`, `WHERE`, `CREATE`, `AS`, etc. |
| **One clause per line** | Each `SELECT` column, `JOIN`, `WHERE` predicate on its own line. |
| **Indent with 2 spaces** | Consistent indentation within subqueries and CTEs. |
| **Trailing commas** | Use trailing commas in `SELECT` lists for cleaner diffs. |

### 3.2 — Naming

| Element | Convention | Example |
|---------|------------|---------|
| Datasets | `snake_case`, prefixed by domain | `air_quality_raw`, `air_quality_staging` |
| Tables | `snake_case`, noun-based | `station_metadata`, `openaq_hourly` |
| Views | `snake_case`, descriptive | `openaq_hourly_latest`, `station_freshness` |
| Columns | `snake_case` | `period_from_utc`, `coverage_pct` |
| Timestamps | Suffix with `_utc` or `_local` to clarify timezone | `period_from_utc`, `run_started_at` |

### 3.3 — Schema Discipline

| Rule | Detail |
|------|--------|
| **Always use `CREATE TABLE IF NOT EXISTS` / `CREATE OR REPLACE VIEW`** | Scripts must be re-runnable without error. |
| **Fully qualify table references in views** | Use `project.dataset.table` to avoid ambiguity, especially in cross-dataset views. Views that reference tables without project qualification break when queried from a different default project. |
| **Partition and cluster intentionally** | Every table expected to exceed 1 GB must be partitioned. Document the choice of partition key and cluster columns in the `OPTIONS(description=...)`. |
| **Set partition expiration for raw data** | Raw append-only tables must have `partition_expiration_days` set to prevent unbounded growth. |
| **`NOT NULL` on identity and timestamp columns** | Primary identifiers (`station_id`, `run_id`, `dedup_key`) and audit timestamps (`ingested_at`) are always `NOT NULL`. |
| **Dedup keys** | Every append-only table must include a deterministic `dedup_key` column. The corresponding staging view uses `ROW_NUMBER() OVER (PARTITION BY dedup_key ORDER BY ingested_at DESC)` to surface only the latest version. |

### 3.4 — SQL File Organisation

| Rule | Detail |
|------|--------|
| **One logical object per file** | Or group tightly related objects (e.g., all dedup views in one file) with a clear header comment per object. |
| **File header** | Every `.sql` file starts with a comment block: purpose, how to run it, and any dependencies. |
| **No inline SQL in Python for schema changes** | Table creation and view definitions live in `.sql` files under `warehouse/`. Python code may contain `SELECT` queries for data reads but not DDL. |

---

## 4 — Infrastructure and Configuration

### 4.1 — Environment Files

| Rule | Detail |
|------|--------|
| **`.env` is never committed** | It contains project-specific values and potentially sensitive overrides. |
| **`.env.example` is always committed** | It lists every variable with a descriptive comment, safe placeholder values, and the expected type/format. |
| **No duplicate keys** | Each variable is defined exactly once. Duplicates cause the last value to silently win, creating confusion. |
| **Group logically** | Group variables by service/concern with comment headers: `# --- GCP ---`, `# --- OpenAQ Poller ---`, `# --- BigQuery ---`. |

### 4.2 — Dockerfiles

| Rule | Detail |
|------|--------|
| **Use a consistent registry** | All images should be pushed to the same Artifact Registry repository. Do not mix `gcr.io` and `REGION-docker.pkg.dev`. |
| **Multi-stage builds** | Use a builder stage for installing dependencies and a slim runtime stage. |
| **Pin base images** | Use a specific Python version tag (e.g., `python:3.12-slim`), never `latest`. |
| **Non-root user** | Containers must run as a non-root user. |
| **`.dockerignore`** | Exclude tests, docs, `.env`, `.git`, and dev tooling from the build context. |

### 4.3 — Infrastructure Scripts

| Rule | Detail |
|------|--------|
| **Idempotent commands** | Every `gcloud` / `bq` command that creates a resource must include `|| true` or the equivalent `--quiet` flag so the runbook is fully re-runnable. |
| **Derive, don't hardcode** | Values like `PROJECT_NUMBER` must be derived at runtime. Project IDs, regions, and names must come from `.env` variables. |
| **No secrets in scripts or env files** | Secrets are stored in Secret Manager and referenced by name only. |

---

## 5 — Testing

### 5.1 — Test Requirements

| Rule | Detail |
|------|--------|
| **Every module must have a corresponding test file** | `poller.py` → `test_poller.py`. |
| **Unit tests cover pure logic** | Transformation functions, parsing, config validation, windowing logic, dedup key generation — all must have unit tests. |
| **Mock external boundaries** | HTTP calls, BigQuery client, Secret Manager client — always mocked in unit tests. Use `unittest.mock.patch` or `pytest-mock`. |
| **Integration tests are separate** | Tests that require real GCP credentials go in a `tests/integration/` directory and are excluded from the default test run. |
| **Minimum coverage target** | 80% line coverage for application code. Config parsing and error handling paths must be covered. |

### 5.2 — Test Organisation

```
tests/
├── unit/
│   ├── test_rate_limiter.py
│   ├── test_progress_tracker.py
│   ├── test_openaq_poller.py
│   ├── test_weather_poller.py
│   └── test_config.py
├── integration/
│   ├── test_bigquery_writes.py
│   └── test_openaq_api.py
└── conftest.py          # shared fixtures
```

### 5.3 — Test Naming

```python
def test_get_query_window_truncates_to_complete_hours(): ...
def test_config_raises_on_missing_project_id(): ...
def test_transform_hour_row_skips_incomplete_period_beyond_window(): ...
```

Use the pattern: `test_<unit_under_test>_<scenario>`.

---

## 6 — Version Control

### 6.1 — Branching and Commits

| Rule | Detail |
|------|--------|
| **Branch naming** | `feature/<short-description>`, `fix/<short-description>`, `chore/<short-description>`. |
| **Commit messages** | Imperative mood, ≤ 72 chars for the subject. Reference an issue number if applicable. Body explains *why*, not *what*. |
| **Small, focused commits** | Each commit should be a single logical change that compiles and passes tests. |
| **No force-push to `main`** | All changes to `main` go through a pull request. |

### 6.2 — Code Review

| Rule | Detail |
|------|--------|
| **Every PR requires at least one approval** before merge. |
| **Review checklist** | Reviewer verifies: tests pass, types check, linter is clean, naming is clear, error handling is present, no secrets in code. |
| **Author runs the full check suite locally** before opening the PR. |

---

## 7 — Security

| Rule | Detail |
|------|--------|
| **No secrets in code, env files, or logs** | API keys and credentials live in Secret Manager. Reference by secret name only. |
| **Validate and sanitise all external input** | API responses, user-provided parameters, and query results should be validated before use. |
| **Least privilege IAM** | Each service account gets only the roles it needs. Prefer resource-level bindings (dataset, topic) over project-level where possible. |
| **Dependency vulnerability scanning** | Run `pip-audit` or Dependabot on every PR. |
| **Container scanning** | Enable Artifact Registry vulnerability scanning for all pushed images. |

---

## 8 — Documentation

| Rule | Detail |
|------|--------|
| **README.md** | Repository root must contain a README covering: project purpose, architecture overview, local setup, how to run each component, and how to deploy. |
| **Runbook is code** | The deployment runbook must be kept in sync with actual infrastructure. Every infrastructure change is reflected in the runbook in the same PR. |
| **ADRs (Architecture Decision Records)** | Major decisions (e.g., "why Cloud Run jobs over Cloud Functions", "why 30-day partition expiration") are documented in `docs/adr/` with date, context, decision, and consequences. |
| **Inline comments explain *why*** | Comments must explain non-obvious intent or business logic. Do not restate what the code does. |

---

## 9 — CI/CD Enforcement

All rules above must be enforced automatically where possible. The CI pipeline must run the following checks on every pull request:

| Check | Tool | Fail on |
|-------|------|---------|
| Formatting | `black --check` | Any unformatted file |
| Import order | `isort --check` | Any misordered import |
| Linting | `ruff check` | Any warning |
| Type checking | `mypy --strict` | Any error |
| Unit tests | `pytest tests/unit/` | Any failure |
| Coverage | `pytest --cov --cov-fail-under=80` | Below threshold |
| Dependency audit | `pip-audit` | Known vulnerabilities |
| SQL lint (optional) | `sqlfluff lint warehouse/` | Fixable violations |

---