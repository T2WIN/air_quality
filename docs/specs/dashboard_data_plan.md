# Dashboard Redesign — Final Plan

## Goal

Redesign the air quality dashboard around the question: **"What's the air quality, and what will conditions be like?"** — leveraging weather *forecasts* to compute a forward-looking dispersion outlook.

---

## Data Available

| Source | Fields | Grain | Retention |
|---|---|---|---|
| OpenAQ | pm25, pm10, no2 values + coverage metrics | Station × hour | 30 days |
| Weather forecasts | temp, humidity, pressure, wind speed/direction, precip, cloud cover, boundary layer height | Station × hour | 48h forecast window, 30-day retention |
| Station metadata | name, locality, lat/lon, pollutants_available | Static | Permanent |
| Ingestion log | run status, duration, records, errors | Per poller run | Permanent |

---

## Analytics Views: 4 total (2 new, 2 existing)

### Keep as-is
- **`v_station_latest_pollutants`** — latest pm25/pm10/no2 per station + metadata. Used by Map + Current Conditions sections.
- **`v_ingestion_overview`** — pipeline run status + 24h aggregates. Used by Pipeline Health section.

### Create new
- **`v_station_hourly_combined`** — pivoted hourly pollutants LEFT JOINed with weather at matching station/hour. Contains: station_id, hour_utc, pm25_value, pm10_value, no2_value, temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m, precipitation, cloud_cover, boundary_layer_height. No metadata columns (those come from `v_station_latest_pollutants`). Serves the Time Series section.

- **`v_station_dispersion_outlook`** — future forecast hours (valid_time > `${REFERENCE_TIMESTAMP}`) with computed dispersion index. Uses `${REFERENCE_TIMESTAMP}` placeholder (substituted to `CURRENT_TIMESTAMP()` in production, evaluated at query time). LEFT JOINs latest PM2.5 per station from `v_openaq_deduped` and station metadata. Serves the Map and Dispersion Outlook sections. Contains:
  - Per-hour raw forecast: temperature, humidity, wind speed/direction, precip, cloud cover, BLH
  - Per-hour dispersion components: blh_score, wind_score, precip_score (each 0–1, where 1 = good dispersion)
  - Per-hour composite: `0.40 × blh_score + 0.35 × wind_score + 0.25 × precip_score`
  - Per-hour category: **poor** (<0.30), **fair** (0.30–0.55), **good** (≥0.55)
  - latest_pm25, latest_pm25_time (from OpenAQ deduped view)
  - station_name, locality, latitude, longitude (from station_metadata)

### Retire from dashboard only (keep in warehouse — existing tests still pass)
- `v_station_hourly_wide` — subsumed by `v_station_hourly_combined`
- `v_station_current_outlook` — subsumed by `v_station_dispersion_outlook`
- `v_station_freshness` — trivial enough to compute inline in dashboard (`CURRENT_TIMESTAMP() - MAX(period_from_utc)`)

---

## Dispersion Index Logic

Three components, each normalized 0–1 where 1 = good dispersion:

| Component | Formula | Rationale |
|---|---|---|
| `blh_score` | `clamp(boundary_layer_height / 1500, 0, 1)` | Low BLH = temperature inversion = traps pollution |
| `wind_score` | `clamp(wind_speed_10m / 25, 0, 1)` | Calm wind = stagnation |
| `precip_score` | `clamp(precipitation / 3, 0, 1)` | Rain washes out PM |

Composite: `0.40 × blh_score + 0.35 × wind_score + 0.25 × precip_score`

Weighted by physical importance — BLH is the dominant factor for pollution trapping, wind is the primary dispersion mechanism, precipitation provides episodic washout.

NULL weather fields score 0 (conservative: assumes worst case).

Categories:
- **poor**: score < 0.30 — conditions trap pollution
- **fair**: 0.30 ≤ score < 0.55 — moderate dispersion
- **good**: score ≥ 0.55 — conditions disperse pollution well

---

## Dashboard Sections

### 1. Station Map
- Data: `v_station_dispersion_outlook` grouped by station → worst dispersion category + latest PM2.5
- Visual: pydeck ScatterplotLayer; color = PM2.5 severity (green/yellow/orange/red AQI bands); marker border or size = worst outlook category
- Tooltip: station name, latest PM2.5, worst outlook category, timestamp

### 2. Current Conditions Table
- Data: `v_station_latest_pollutants`
- Visual: sortable `st.dataframe` with AQI color bands on PM2.5/PM10/NO2 columns
- Freshness: computed inline as `TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), latest_reading_time, HOUR)`, displayed as "Xh ago"
- Stations with stale data (>2h) highlighted

### 3. Station Time Series
- Data: `v_station_hourly_combined` WHERE station_id = ? AND hour_utc >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL N DAY)
- Selectors: station (dropdown), pollutant (pm25/pm10/no2), weather overlay (temperature/wind speed/BLH/precipitation/none), days back (slider 1–14, default 3)
- Visual: Altair dual-axis line chart — pollutant value (left Y, solid) + weather variable (right Y, dashed)
- Expander: `st.line_chart` for additional weather variables

### 4. Dispersion Outlook
- Data: `v_station_dispersion_outlook` WHERE station_id = ?
- Station selector (dropdown)
- Visual: Altair chart — 24h timeline with dispersion score as a bar/area chart, colored by category (red=fair, green=good, red=poor). Raw forecast detail in an expander (temperature, wind, BLH, precip as line charts)

### 5. Pipeline Health
- Data: `v_ingestion_overview`
- Visual: `st.metric` cards per source (openaq, open-meteo)
- Color by freshness: green (<90 min), yellow (<360 min), red (≥360 min)
- Delta shows records written + duration

---

## Environment Variables

The dashboard rewrite must use env vars for dataset names (currently hardcoded). Required env vars passed via `--set-env-vars` at Cloud Run deploy:

```
PROJECT_ID
BQ_RAW_DATASET
BQ_STAGING_DATASET
BQ_ANALYTICS_DATASET
```

For local dev: `set -a && source .env && set +a` (existing pattern).

---

## Fixture Gap

Current `seed_core.sql` has weather forecasts at valid_times 11:00–14:00 UTC for station_a, but reference timestamp is 14:00 UTC. The dispersion outlook filters `valid_time > reference`, yielding zero future rows. Also, no weather hours overlap with pollutant hours (07:00–10:00), so the combined join has nothing to test.

There are two primary issues with the current fixtures:
1. Future weather rows for station_a already exist in `seed_core.sql`, but contain incorrect values (e.g., BLH=800/1400/1300), which produce 'fair/good/good' categories and miss the 'poor' category entirely.
2. station_c has no future weather rows, making it impossible to test the "weather-only station with NULL pm25" edge case in the dispersion outlook.

**Update `seed_core.sql`**:
- **Station A (Correct existing 3 future rows)**:
  - 15:00: BLH=200, wind=3, precip=0 → score ~0.10 → **poor**
  - 16:00: BLH=1200, wind=15, precip=2 → score ~0.70 → **good**
  - 17:00: BLH=800, wind=8, precip=0.5 → score ~0.42 → **fair**
- **Station A (Fix 2 overlap rows)**:
  - 09:00 and 10:00 UTC (overlaps pollutant hours). The 10:00 row must match assertion expectations: temperature_2m=9.0, wind_speed_10m=4.5, boundary_layer_height=550.0.
- **Station C (Add 2 future rows)**:
  - 15:00 and 16:00 UTC (reasonable values, e.g., BLH=1000, wind=5, precip=0) to test NULL pm25.

Verified: these additions don't break existing `v_station_current_outlook` assertions (the 3h outlook window is valid_time > 10:00 AND <= 13:00, so 09:00, 10:00, 15:00+ are excluded).

---

## Test Assertions

### `v_station_hourly_combined` (2 assertions)

1. **`v_station_hourly_combined__pollutant_weather_join_is_correct.sql`**
   - Station A at 10:00 UTC should have pm25=22, pm10=30, no2=45, temperature_2m=9.0, wind_speed_10m=4.5, boundary_layer_height=550.0
   - Pattern: `WITH expected AS (hardcoded), actual AS (SELECT from view), LEFT JOIN ... WHERE mismatch`

2. **`v_station_hourly_combined__null_weather_hours_exist.sql`**
   - Station A at 07:00 UTC should have pm25=50.0 but NULL temperature_2m (no matching weather row at that hour)
   - Pattern: same as above, asserting NULL

### `v_station_dispersion_outlook` (3 assertions)

1. **`v_station_dispersion_outlook__only_future_hours_returned.sql`**
   - All returned valid_times should be > reference timestamp (15:00, 16:00, 17:00 for station_a; 15:00, 16:00 for station_c)
   - Pattern: `SELECT ... FROM view WHERE valid_time <= ${REFERENCE_TIMESTAMP}` → should return 0 rows

2. **`v_station_dispersion_outlook__dispersion_scores_are_correct.sql`**
   - Station A at 15:00: blh_score=0.133, wind_score=0.12, precip_score=0, dispersion_score≈0.095, category='poor'
   - Station A at 16:00: blh_score=0.80, wind_score=0.60, precip_score≈0.667, dispersion_score≈0.697, category='good'
   - Pattern: `WITH expected AS (hardcoded scores), actual AS (SELECT from view)` with float tolerance `ABS(...) > 0.01`

3. **`v_station_dispersion_outlook__latest_pm25_attached.sql`**
   - Station A should have latest_pm25=22.0 (latest PM2.5 from OpenAQ deduped data at 10:00 UTC)
   - Station C should have latest_pm25 IS NULL (weather-only station, no OpenAQ data)
   - Pattern: cardinality check + value check

---

## Files to Change

| # | File | Action | Detail |
|---|---|---|---|
| 1 | `tests/bq_views/fixtures/seed_core.sql` | Edit | Fix values of existing 3 future rows + 2 overlap rows for station_a; add 2 future rows for station_c |
| 2 | `warehouse/analytics/v_station_hourly_combined.sql` | **Create** | Pivoted hourly pollutants LEFT JOIN weather at station/hour; no metadata columns |
| 3 | `warehouse/analytics/v_station_dispersion_outlook.sql` | **Create** | Future forecast hours with dispersion index + latest PM2.5 + metadata; uses `${REFERENCE_TIMESTAMP}` |
| 4 | `tests/bq_views/view_manifest.json` | Edit | Add 2 entries: `v_station_hourly_combined` (stage 2, order 3, deps: v_openaq_deduped, v_weather_deduped) and `v_station_dispersion_outlook` (stage 2, order 4, deps: v_openaq_deduped, v_weather_deduped) |
| 5 | `tests/bq_views/assertions/v_station_hourly_combined__pollutant_weather_join_is_correct.sql` | **Create** | |
| 6 | `tests/bq_views/assertions/v_station_hourly_combined__null_weather_hours_exist.sql` | **Create** | |
| 7 | `tests/bq_views/assertions/v_station_dispersion_outlook__only_future_hours_returned.sql` | **Create** | |
| 8 | `tests/bq_views/assertions/v_station_dispersion_outlook__dispersion_scores_are_correct.sql` | **Create** | |
| 9 | `tests/bq_views/assertions/v_station_dispersion_outlook__latest_pm25_attached.sql` | **Create** | |
| 10 | `scripts/create_infra.sh` | Edit | Step 8: add 2 `envsubst \| bq query` lines for new views. Step 16: dashboard deploy (image already built in step 11 via cloudbuild.yaml) with `--set-env-vars` for PROJECT_ID/BQ datasets, `--allow-unauthenticated`, service account = `DASHBOARD_READER_SERVICE_ACCOUNT_NAME` |
| 11 | `cloudbuild.yaml` | Edit | Add dashboard build step (3 containers total); single gcloud builds submit call in create_infra.sh step 11 builds all images |
| 12 | `scripts/validate-infra.sh` | Edit | Add existence checks for all 7 analytics views (5 existing + 2 new) using `bq show` pattern |
| 13 | `dashboard/app.py` | **Rewrite** | 5 sections against 4 analytics views; use env vars for dataset names |

---

## Execution Order

1. Add fixture data to `seed_core.sql`
2. Create `v_station_hourly_combined.sql`
3. Create `v_station_dispersion_outlook.sql`
4. Update `view_manifest.json`
5. Create 5 assertion SQL files
6. Update `scripts/create_infra.sh` (step 8 + step 16 dashboard deploy only)
7. Update `cloudbuild.yaml` (add dashboard build step; single build call builds all images)
8. Update `scripts/validate-infra.sh` (analytics view checks)
9. Run warehouse view tests: `cd tests/bq_views && python run_view_tests.py`
10. Rewrite `dashboard/app.py`
11. Run linting/type checks per `AGENTS.md`: `black --check`, `isort --check`, `ruff check`, `mypy --strict`
12. Deploy new views to GCP (manual `envsubst | bq query` for the 2 new views, or re-run `create_infra.sh`)
13. Build & deploy dashboard to Cloud Run
14. Run `bash scripts/validate-infra.sh $PROJECT_ID`
15. Local smoke test of dashboard