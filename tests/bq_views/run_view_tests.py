"""
BigQuery View Test Runner

One-command test system for BigQuery views:
- Creates temporary datasets
- Seeds deterministic fixture data
- Creates candidate views
- Runs SQL-based assertions
- Produces PASS/FAIL output and JSON report
"""

from __future__ import annotations

import argparse
import json
import os
import random
import string
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError


# =============================================================================
# Path Constants (module-relative, not CWD-dependent)
# =============================================================================

TESTS_DIR = Path(__file__).resolve().parent          # tests/bq_views/
REPO_ROOT = TESTS_DIR.parent.parent                  # repository root

SCHEMA_PATH = TESTS_DIR / "schemas" / "raw_tables.sql"
FIXTURES_PATH = TESTS_DIR / "fixtures" / "seed_core.sql"
MANIFEST_PATH = TESTS_DIR / "view_manifest.json"
ASSERTIONS_DIR = TESTS_DIR / "assertions"
DEFAULT_REPORT_PATH = TESTS_DIR / ".reports" / "view_test_report.json"


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True)
class Config:
    """Test runner configuration loaded from environment."""

    dev_project_id: str
    bq_location: str
    raw_dataset: str
    staging_dataset: str
    analytics_dataset: str

    # Derived test-specific values
    reference_timestamp: str = "TIMESTAMP '2026-03-22 14:00:00 UTC'"

    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables."""
        load_dotenv()

        required = [
            "DEV_PROJECT_ID",
            "BQ_LOCATION",
            "BQ_RAW_DATASET",
            "BQ_STAGING_DATASET",
            "BQ_ANALYTICS_DATASET",
        ]

        missing = [var for var in required if not os.environ.get(var)]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        return cls(
            dev_project_id=os.environ["DEV_PROJECT_ID"],
            bq_location=os.environ["BQ_LOCATION"],
            raw_dataset=os.environ["BQ_RAW_DATASET"],
            staging_dataset=os.environ["BQ_STAGING_DATASET"],
            analytics_dataset=os.environ["BQ_ANALYTICS_DATASET"],
        )


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class TestResult:
    """Result of a single assertion test."""

    name: str
    status: str  # "passed", "failed", "error"
    violations: list[dict[str, Any]] = field(default_factory=list)
    error_message: str = ""


@dataclass
class TestRun:
    """Complete test run state and results."""

    run_id: str
    project_id: str
    location: str
    raw_dataset: str
    staging_dataset: str
    analytics_dataset: str
    results: list[TestResult] = field(default_factory=list)
    setup_error: str = ""  # Fatal setup error that prevented tests from running

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == "error")

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def has_any_failure(self) -> bool:
        """True if any test failed, errored, or there was a setup error."""
        return self.failed > 0 or self.errors > 0 or bool(self.setup_error)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "location": self.location,
            "datasets": {
                "raw": self.raw_dataset,
                "staging": self.staging_dataset,
                "analytics": self.analytics_dataset,
            },
            "status": "failed" if self.has_any_failure else "passed",
            "summary": {
                "tests_total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "errors": self.errors,
            },
            "tests": [
                {
                    "name": r.name,
                    "status": r.status,
                    **({"violations": r.violations} if r.violations else {}),
                    **({"error_message": r.error_message} if r.error_message else {}),
                }
                for r in self.results
            ],
        }
        if self.setup_error:
            result["setup_error"] = self.setup_error
        return result


# =============================================================================
# BigQuery Client Wrapper
# =============================================================================


class BigQueryTestClient:
    """Wrapper for BigQuery operations in test context."""

    def __init__(self, project_id: str, location: str):
        self.client = bigquery.Client(project=project_id, location=location)
        self.project_id = project_id
        self.location = location

    def create_dataset(self, dataset_id: str) -> None:
        """Create a dataset in the test project."""
        dataset_ref = f"{self.project_id}.{dataset_id}"
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = self.location
        self.client.create_dataset(dataset, exists_ok=True)

    def drop_dataset(self, dataset_id: str) -> None:
        """Drop a dataset and all its contents."""
        dataset_ref = f"{self.project_id}.{dataset_id}"
        try:
            self.client.delete_dataset(
                dataset_ref, delete_contents=True, not_found_ok=True
            )
        except GoogleAPICallError as e:
            print(f"[warn] Failed to drop dataset {dataset_id}: {e}")

    def execute_sql(self, sql: str, dry_run: bool = False) -> bigquery.QueryJob:
        """Execute a SQL query and return the job. Waits for completion."""
        job_config = bigquery.QueryJobConfig(dry_run=dry_run, use_query_cache=False)
        job = self.client.query(sql, job_config=job_config)
        # Wait for job completion (important for DDL statements)
        job.result()
        return job

    def query_to_dataframe(self, sql: str):
        """Execute a query and return results as a pandas DataFrame."""
        job = self.client.query(sql)
        return job.to_dataframe()


# =============================================================================
# SQL Template Renderer
# =============================================================================


def render_sql_template(sql: str, placeholders: dict[str, str]) -> str:
    """Replace ${VAR} style placeholders in SQL template with actual values."""
    result = sql
    for key, value in placeholders.items():
        result = result.replace(f"${{{key}}}", value)
    return result


def get_placeholders(config: Config, run: TestRun) -> dict[str, str]:
    """Generate placeholder mapping for SQL templates using env var names."""
    return {
        "PROJECT_ID": config.dev_project_id,
        "BQ_RAW_DATASET": run.raw_dataset,
        "BQ_STAGING_DATASET": run.staging_dataset,
        "BQ_ANALYTICS_DATASET": run.analytics_dataset,
        "REFERENCE_TIMESTAMP": config.reference_timestamp,
    }


# =============================================================================
# Dataset Management
# =============================================================================


def generate_run_id() -> str:
    """Generate a unique run ID: timestamp + 4 random lowercase chars."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    random_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{timestamp}_{random_suffix}"


def create_temp_datasets(
    client: BigQueryTestClient, config: Config, run_id: str
) -> tuple[str, str, str]:
    """Create temporary datasets for test run."""
    raw_dataset = f"{config.raw_dataset}_test_{run_id}"
    staging_dataset = f"{config.staging_dataset}_test_{run_id}"
    analytics_dataset = f"{config.analytics_dataset}_test_{run_id}"

    print("[setup] Creating temp datasets...")
    client.create_dataset(raw_dataset)
    client.create_dataset(staging_dataset)
    client.create_dataset(analytics_dataset)

    print(f"[setup] raw_dataset={raw_dataset}")
    print(f"[setup] staging_dataset={staging_dataset}")
    print(f"[setup] analytics_dataset={analytics_dataset}")

    return raw_dataset, staging_dataset, analytics_dataset


def drop_temp_datasets(
    client: BigQueryTestClient, raw: str, staging: str, analytics: str
) -> None:
    """Drop all temporary datasets."""
    print("[cleanup] Dropping temp datasets...")
    client.drop_dataset(raw)
    client.drop_dataset(staging)
    client.drop_dataset(analytics)


# =============================================================================
# View Manifest Loading
# =============================================================================


def load_view_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    """Load and return the view manifest ordered by stage."""
    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    # Sort by stage number, then by order within stage
    return sorted(manifest["views"], key=lambda v: (v["stage"], v.get("order", 0)))


# =============================================================================
# SQL File Loading
# =============================================================================


def load_sql_file(path: Path) -> str:
    """Load SQL from file, return empty string if not found."""
    try:
        with open(path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def get_assertion_files(
    assertions_dir: Path, only_pattern: str | None = None
) -> list[Path]:
    """Get all assertion SQL files, optionally filtered by pattern."""
    if not assertions_dir.exists():
        return []

    files = sorted(assertions_dir.glob("*.sql"))

    if only_pattern:
        files = [f for f in files if only_pattern in f.name]

    return files


# =============================================================================
# Test Execution
# =============================================================================


def create_views(
    client: BigQueryTestClient,
    views: list[dict[str, Any]],
    placeholders: dict[str, str],
) -> list[TestResult]:
    """Create all views in dependency order.

    Always stops on first error because downstream views depend on
    upstream ones — continuing would produce cascading failures.
    """
    results = []

    print("\n[views] Creating views...")
    for view in views:
        view_name = view["name"]
        # View file paths in the manifest are repo-root-relative
        file_path = REPO_ROOT / view["file_path"]

        sql_template = load_sql_file(file_path)
        if not sql_template:
            msg = f"View file not found: {file_path}"
            print(f"[views] ERROR {view_name}: {msg}")
            results.append(
                TestResult(name=f"view__{view_name}", status="error", error_message=msg)
            )
            break

        sql = render_sql_template(sql_template, placeholders)

        try:
            client.execute_sql(sql)
            print(f"[views] PASS {view_name}")
        except Exception as e:
            msg = str(e)
            print(f"[views] FAIL {view_name}: {msg}")
            results.append(
                TestResult(name=f"view__{view_name}", status="error", error_message=msg)
            )
            break

    return results


def run_assertions(
    client: BigQueryTestClient,
    assertions_dir: Path,
    placeholders: dict[str, str],
    only_pattern: str | None = None,
    stop_on_first_failure: bool = False,
) -> list[TestResult]:
    """Run all assertion SQL files and return results."""
    results = []
    files = get_assertion_files(assertions_dir, only_pattern)

    print("\n[assert] Running assertions...")
    for file_path in files:
        test_name = file_path.stem
        sql_template = load_sql_file(file_path)

        if not sql_template.strip():
            results.append(
                TestResult(
                    name=test_name, status="error", error_message="Empty SQL file"
                )
            )
            print(f"[assert] ERROR {test_name}: Empty SQL file")
            continue

        sql = render_sql_template(sql_template, placeholders)

        try:
            df = client.query_to_dataframe(sql)

            if len(df) == 0:
                results.append(TestResult(name=test_name, status="passed"))
                print(f"[assert] PASS {test_name}")
            else:
                violations = df.head(5).to_dict("records")
                results.append(
                    TestResult(name=test_name, status="failed", violations=violations)
                )
                print(f"[assert] FAIL {test_name}")
                print(f"         violating_rows={len(df)}")
                if violations:
                    sample = json.dumps(violations[0], default=str)
                    print(f"         sample={sample}")

                if stop_on_first_failure:
                    break

        except Exception as e:
            results.append(
                TestResult(name=test_name, status="error", error_message=str(e))
            )
            print(f"[assert] ERROR {test_name}: {e}")
            if stop_on_first_failure:
                break

    return results


def write_report(run: TestRun, report_path: Path) -> None:
    """Write JSON test report to file."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(run.to_dict(), f, indent=2, default=str)
    print(f"\n[report] Written to {report_path}")


# =============================================================================
# Setup Phase Execution
# =============================================================================


def execute_setup_phase(
    client: BigQueryTestClient,
    run: TestRun,
    placeholders: dict[str, str],
) -> list[TestResult]:
    """Execute schema creation and fixture seeding. Returns any error results."""
    results = []

    # Load and execute schema
    schema_sql_raw = load_sql_file(SCHEMA_PATH)

    if not schema_sql_raw.strip():
        msg = f"Schema file missing or empty: {SCHEMA_PATH}"
        print(f"[setup] ERROR: {msg}")
        results.append(
            TestResult(name="setup__raw_tables", status="error", error_message=msg)
        )
        return results

    schema_sql = render_sql_template(schema_sql_raw, placeholders)
    print("[setup] Creating raw tables...")
    try:
        client.execute_sql(schema_sql)
        print("[setup] PASS raw tables created")
    except Exception as e:
        msg = str(e)
        print(f"[setup] ERROR creating raw tables: {msg}")
        results.append(
            TestResult(name="setup__raw_tables", status="error", error_message=msg)
        )
        return results

    # Load and execute fixtures
    fixtures_sql_raw = load_sql_file(FIXTURES_PATH)

    if not fixtures_sql_raw.strip():
        msg = f"Fixture file missing or empty: {FIXTURES_PATH}"
        print(f"[setup] ERROR: {msg}")
        results.append(
            TestResult(name="setup__seed_core", status="error", error_message=msg)
        )
        return results

    fixtures_sql = render_sql_template(fixtures_sql_raw, placeholders)
    print("[setup] Seeding fixtures...")
    try:
        client.execute_sql(fixtures_sql)
        print("[setup] PASS fixtures seeded")
    except Exception as e:
        msg = str(e)
        print(f"[setup] ERROR seeding fixtures: {msg}")
        results.append(
            TestResult(name="setup__seed_core", status="error", error_message=msg)
        )
        return results

    return results


# =============================================================================
# Main Entry Point
# =============================================================================


def main() -> int:
    """Main entry point for the test runner."""
    parser = argparse.ArgumentParser(description="BigQuery View Test Runner")
    parser.add_argument(
        "--report-path",
        default=str(DEFAULT_REPORT_PATH),
        help="Path for JSON report output",
    )
    parser.add_argument(
        "--only", default=None, help="Only run tests matching this pattern"
    )
    parser.add_argument(
        "--stop-on-first-failure",
        action="store_true",
        help="Stop immediately on first failure",
    )
    parser.add_argument(
        "--keep-datasets-on-failure",
        action="store_true",
        help="Don't drop temp datasets if tests fail",
    )

    args = parser.parse_args()

    # Load configuration
    try:
        config = Config.from_env()
    except ValueError as e:
        print(f"[error] Configuration error: {e}")
        # Write minimal report for config errors
        report_path = Path(args.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(
                {
                    "status": "failed",
                    "setup_error": f"Configuration error: {e}",
                    "summary": {
                        "tests_total": 0,
                        "passed": 0,
                        "failed": 0,
                        "errors": 0,
                    },
                    "tests": [],
                },
                f,
                indent=2,
            )
        print(f"[report] Written to {report_path}")
        return 1

    run_id = generate_run_id()

    print(f"[setup] project={config.dev_project_id} location={config.bq_location}")
    print(f"[setup] run_id={run_id}")

    # Initialize BigQuery client
    client = BigQueryTestClient(config.dev_project_id, config.bq_location)

    # Create temp datasets
    raw_ds, staging_ds, analytics_ds = create_temp_datasets(client, config, run_id)

    run = TestRun(
        run_id=run_id,
        project_id=config.dev_project_id,
        location=config.bq_location,
        raw_dataset=raw_ds,
        staging_dataset=staging_ds,
        analytics_dataset=analytics_ds,
    )

    placeholders = get_placeholders(config, run)
    all_results: list[TestResult] = []

    try:
        # Execute setup phase (schema + fixtures)
        setup_results = execute_setup_phase(client, run, placeholders)
        all_results.extend(setup_results)

        # If setup failed, skip views and assertions
        if any(r.status == "error" for r in setup_results):
            run.setup_error = "Setup phase failed - see test results for details"
        else:
            # Load view manifest and create views
            if not MANIFEST_PATH.exists():
                msg = f"View manifest not found: {MANIFEST_PATH}"
                print(f"[error] {msg}")
                all_results.append(
                    TestResult(
                        name="setup__manifest", status="error", error_message=msg
                    )
                )
                run.setup_error = msg
            else:
                views = load_view_manifest(MANIFEST_PATH)
                view_results = create_views(
                    client,
                    views,
                    placeholders,
                )
                all_results.extend(view_results)

                # Only run assertions if views were created successfully
                if not any(r.status == "error" for r in view_results):
                    assertion_results = run_assertions(
                        client,
                        ASSERTIONS_DIR,
                        placeholders,
                        only_pattern=args.only,
                        stop_on_first_failure=args.stop_on_first_failure,
                    )
                    all_results.extend(assertion_results)

    except Exception as e:
        # Catch any unexpected errors
        error_msg = f"Unexpected error: {str(e)}\n{traceback.format_exc()}"
        print(f"\n[error] {error_msg}")
        run.setup_error = error_msg

    finally:
        # Store all results in the run object
        run.results = all_results

        # Write report BEFORE cleanup so it's always available
        report_path = Path(args.report_path)
        write_report(run, report_path)

        # Cleanup logic - respect --keep-datasets-on-failure
        if args.keep_datasets_on_failure and run.has_any_failure:
            print(f"\n[keep] Datasets preserved for debugging:")
            print(f"       raw: {raw_ds}")
            print(f"       staging: {staging_ds}")
            print(f"       analytics: {analytics_ds}")
        else:
            drop_temp_datasets(client, raw_ds, staging_ds, analytics_ds)

    # Print summary
    status = "PASSED" if not run.has_any_failure else "FAILED"
    print(
        f"\n[result] {status} tests={run.total} passed={run.passed} failed={run.failed} errors={run.errors}"
    )

    return 0 if not run.has_any_failure else 1


if __name__ == "__main__":
    raise SystemExit(main())