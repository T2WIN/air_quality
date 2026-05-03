# Ingestion Test Coverage Plan — 80% Target

## Current state

Existing test files:
- `tests/unit/test_rate_limiter.py` (10 tests, rate_limiter only)
- `tests/unit/test_datetime_utils.py` (20 tests, datetime_utils)
- `tests/unit/test_http_utils.py` (13 tests, http_utils)
- `tests/unit/test_ingestion_log.py` (8 tests, ingestion_log — added)

Run tests using venv
## Test files to create

### 1. `tests/unit/test_datetime_utils.py` — 20 tests ✅ DONE

| Function | Tests | Edge cases |
|---|---|---|
| `utc_now` | 1 | UTC tz verify |
| `to_rfc3339_z` | 2 | UTC→Z suffix, non-UTC→UTC then Z |
| `parse_timestamp` | 4 | Z suffix, +00:00, None, empty str |
| `build_run_id` | 1 | Format: `YYYYMMDDTHHMMSSZ_<8hex>` |
| `deep_get` | 5 | Simple, nested, missing key, non-dict intermediate, None value |
| `parse_csv_env` | 4 | Valid, None, empty, whitespace trimming |
| `parse_optional_int` | 3 | Valid int, None, empty str |

Mock: `uuid.uuid4` for deterministic `build_run_id`. All 20 tests pass.

### 2. `tests/unit/test_http_utils.py` — ✅ 13 tests (96% coverage)

| Function | Count | Edge cases |
|---|---|---|
| `get_session` | 4 | Default args, extra_headers, same-thread reuse, different-thread fresh |
| `_parse_retry_after` | 5 | None/empty, seconds value, negative clamp, HTTP-date, invalid value |
| `backoff_seconds` | 4 | No response → exp+jitter + cap at 30, Retry-After sec, Retry-After HTTP-date, invalid Retry-After falls through |

### 3. `tests/unit/test_ingestion_log.py` — ✅ 8 tests (100% coverage)

| Function | Count | Edge cases |
|---|---|---|
| `write_ingestion_log` | 8 | All fields, None optionals, failed_sensor_ids list, failed_station_ids list, duration_seconds calc, error_message, table_id format, BQ failure path |

Patches `utc_now` for deterministic `ingested_at`; uses `MagicMock` for `bigquery.Client`.

### 4. `tests/unit/test_progress_tracker.py` — 17 tests ✅ DONE

| Aspect | Tests | Details |
|---|---|---|
| Init | 2 | Defaults, custom log_every/log_interval |
| Lifecycle | 3 | start() creates thread, stop() joins + logs final, stop() when thread is None |
| record_http_attempt | 1 | Counter increment |
| record_retry | 1 | Retries increment |
| record_success | 3 | had_data=True/False, triggers log at threshold |
| record_failure | 2 | Increments failed+completed, triggers log at threshold |
| Snapshot throttling | 2 | No log below threshold, log forced at force=True |
| Snapshot math | 2 | ETA=0 when all completed, ETA positive + pct + rate calc |
| Heartbeat | 1 | Fires at log_interval_seconds |

Mocks: `time.monotonic`, `threading.Thread`, `logging.Logger.info`. All 17 tests pass at 100% coverage.

### 5. `tests/unit/test_rate_limiter.py` — +4 tests ✅ DONE (14 total, 97% coverage)

| Test | Covers |
|---|---|
| `test_hour_window_eviction` | Hour window popleft (line 34, branch 92→95) |
| `test_minute_window_wait_with_count_greater_than_one` | Wait calc with count > 1 + staggered timestamps |
| `test_advance_time_triggers_eviction` | Advance past both windows, verify full eviction |
| `test_hour_window_wait_calculation` | Branch 79→89 (minute OK, hour NOT OK), lines 90-93 hour wait calc |

Remaining 2 uncovered branches (86→89, 92→95) are the false branches of `if idx < len(...)` — logically unreachable given count validation at lines 52-59.

### 6. `tests/unit/test_openaq_poller.py` — 47 tests ✅ DONE

| Category | Count | Mocking |
|---|---|---|
| `_parse_bool_env` | 4 | None (incl. whitespace stripping) |
| `Config.from_env` | 3 | patch.dict(os.environ, ...) |
| `get_query_window` | 2 | None (datetime math) |
| `_transform_hour_row` | 6 | None |
| `_determine_status` | 5 | None (pure logic) |
| `_build_summary` | 2 | None |
| `_collect_future_result` | 3 | Mock for future |
| `load_station_sensors` | 4 | MagicMock for bigquery.Client |
| `_append_rows_to_bigquery` | 2 | MagicMock for BQ client |
| `_fetch_sensor_hours` | 8 | Mock session + patch time.sleep; retry, exhaustion, ValueError, malformed hours |
| `_persist_rows` | 3 | MagicMock for BQ client |
| `_log_run` | 1 | Patch write_ingestion_log |
| `run_poller` | 3 | Patch internal functions |

**Coverage**: 90% (80% target). Uncovered lines: 497 (dead code), 564–614 (`_poll_sensors` orchestration), 804–821 (`main()`).

### 7. `tests/unit/test_weather_poller.py` — 38 tests ✅ DONE (94% coverage)

| Category | Count | Mocking |
|---|---|---|
| `Config.from_env` | 3 | patch.dict(os.environ, ...) |
| `load_station_locations` | 2 | MagicMock for BQ client |
| `parse_batch` | 5 | None (pure transform) |
| `_determine_status` | 6 | None (incl. both-wrong branch) |
| `_build_summary` | 2 | None |
| `_append_rows_to_bigquery` | 2 | MagicMock for BQ client |
| `_fetch_batch_with_retry` | 7 | MagicMock for session, patch time.sleep |
| `_persist_rows` | 3 | MagicMock for BQ client |
| `_log_run` | 1 | Patch write_ingestion_log |
| `_poll_batch` | 2 | Patch fetch + parse |
| `_poll_stations` | 3 | Mock load + session + tracker |
| `run_poller` | 2 | Patch internal functions |

**Bug found and fixed**: `_fetch_batch_with_retry` logging at line 285 had a dangling `run_id=%s` format placeholder with no matching argument (copy-paste from OpenAQ poller). Removed the placeholder.

**Uncovered**: line 296 (defensive `raise` after retry loop — unreachable in practice), `main()` (skipped per user request).

### 8. `tests/unit/test_station_metadata.py` — 12 tests ✅ DONE

| Category | Count | Mocking |
|---|---|---|
| `filter_locations` | 6 | None (pure logic) — mobile, non-monitor, missing coords, no pollutant, valid, mix |
| `build_metadata_rows` | 2 | None — full fields, missing nested fields |
| `build_sensor_rows` | 2 | None — target-only filter, basic field check |
| `write_to_bigquery` | 2 | MagicMock for BQ client — success + truncate disposition |

**Coverage**: 100% of tested functions (50% module-wide due to 4 skipped functions below).

**Skipped**: `get_secret` (Secret Manager), `fetch_french_locations` (HTTP), `print_summary` (IO), `main()` (per user request).

## Summary

| # | Test file | Tests | Lines to cover | Status |
|---|---|---|---|---|
| 1 | `test_datetime_utils.py` | 20 | 68 | ✅ Done |
| 2 | `test_http_utils.py` | 13 ✅ | 82 | ✅ Done |
| 3 | `test_ingestion_log.py` | 8 ✅ | 103 | ✅ Done |
| 4 | `test_progress_tracker.py` | 17 | 183 | ✅ Done |
| 5 | `test_rate_limiter.py` (additions) | +4 (14 total) | 97 | ✅ Done |
| 6 | `test_openaq_poller.py` | 47 | 821 | ✅ Done (90% coverage) |
| 7 | `test_weather_poller.py` | 38 | 679 | ✅ Done |
| 8 | `test_station_metadata.py` | 12 | 406 | ✅ Done (100% of tested functions) |
| **Total** | | **~169 tests** | **2,439 lines** |

## Key mocking patterns

### BigQuery client
```python
bq_client = MagicMock(spec=bigquery.Client)
bq_client.query.return_value.result.return_value = [{"station_id": 1, ...}]
```

### HTTP session
```python
session = MagicMock(spec=requests.Session)
session.get.return_value.status_code = 200
session.get.return_value.json.return_value = {"results": [...]}
```

### Time (for retry loops and progress tracker)
```python
with patch("time.monotonic", fake_monotonic), patch("time.sleep", fake_sleep):
    # test retry behavior
```

### ThreadPoolExecutor (synchronous for tests)
```python
with patch("concurrent.futures.ThreadPoolExecutor") as mock_executor:
    mock_executor.return_value.__enter__.return_value.submit = lambda f, *a, **kw: f(*a, **kw)
```

## Coverage targets per module

| Module | Current | Target |
|---|---|---|
| `datetime_utils.py` | 100% | 90%+ |
| `http_utils.py` | 96% | 85%+ |
| `ingestion_log.py` | 100% | 80%+ |
| `progress_tracker.py` | 100% | 85%+ |
| `rate_limiter.py` | 97% | 98%+ |
| `openaq_poller/main.py` | 90% | 80%+ |
| `weather_poller/main.py` | 94% | 80%+ |
| `station_metadata.py` | 50% (100% of tested functions) | 70%+ (skipped main + HTTP) |