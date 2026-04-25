# Air Quality Platform — Project Log

Append-only log of significant project events, decisions, and milestones.

---

## 2026-03-21 — Agent instructions and skills initialized
- **What**: Added agent instruction file, 8 skills, validation script, and memory system.
- **Why**: Enable structured agentic development workflow.
- **Impact**: All future agent sessions follow defined protocols.
- **Key files**: .kilo/instructions.md, .kilo/skills/*, scripts/validate-infra.sh, memory/


## 2026-03-22 - Finished testing the validation script
- **What** Updated the validation script and had an agent test it. Found some issues in both the script but also the runbook. So updated the runbook.
- **Why** To ensure the script works as expected.
- **Impact** The script is now working as expected.
- **Key files** scripts/validate-infra.sh, RUNBOOK.md


## 2026-03-22 — BigQuery View Test System
- **What**: Built test system to validate SQL view definitions. Contains a python script that creates a test environment, runs the views, and validates the results and SQL assertions that define the expected results.
- **Why**: To allow easy testing of views by me or agents.
- **Impact**: Much faster testing of views.
- **Key files**: "warehouse/tests/assertions/*.sql" "warehouse/tests/run_view_tests.py" "warehouse/tests/fixtures/*"
- **Verification**: All 20 tests pass against dev project air-quality-test-490920.

## 2026-03-22 — RUNBOOK update and dev deployment
- **What**: Updated RUNBOOK.md (Steps 7, 8, 16) and deployed to dev. Fixed staging views, added analytics views deployment step, removed obsolete dashboard SQL reference, deleted obsolete file.
- **Why**: Improve runbook accuracy and completeness.
- **Impact**: Dev project now has all staging and analytics views deployed and validated.
- **Key files**: RUNBOOK.md, warehouse/viz/create_analytics_views.sql (deleted)
- **Verification**: validate-infra passed 18/18 checks, all views queryable.

## 2026-04-20 — Turned RUNBOOK into a runnable bashfile
- **What**: Converted RUNBOOK.md to executable bash script with validation checks (I have the thought of moving to Terraform after this).
- **Why**: Automate deployment validation process (I tried the runbook as documentation but I realized I wanted to use it as an automated way to setup the cloud infrastructure).
- **Impact**: Faster, more reliable deployment verification.
- **Key files**: scripts/validate-infra.sh
- **Verification**: Script executes all steps and validates results.

## 2026-04-22 — Dashboard Data Plan: Steps 1-8 Completed
- **What**: Implemented first 8 steps of dashboard redesign plan. Updated seed_core.sql fixtures, modified v_station_hourly_combined and v_station_dispersion_outlook views, added entries to view_manifest.json, created 5 new assertion SQL files, updated create_infra.sh, deployed new views to GCP dev.
- **Why**: Foundation for dashboard redesign - new analytics views and corrected test fixtures.
- **Impact**: All 25 warehouse view tests pass. Both new views deployed to GCP dev project (air-quality-test-490920) and ready for dashboard consumption.
- **Key files**:
  - tests/bq_views/fixtures/seed_core.sql (updated)
  - warehouse/analytics/v_station_hourly_combined.sql (modified)
  - warehouse/analytics/v_station_dispersion_outlook.sql (fixed)
  - tests/bq_views/view_manifest.json (updated)
  - tests/bq_views/assertions/* (5 new files)
  - scripts/create_infra.sh (updated) - added v_station_hourly_combined and v_station_dispersion_outlook deployment in Step 8
- **Notes**: BigQuery CLAMP replaced with LEAST/GREATEST; ${REFERENCE_TIMESTAMP} syntax corrected.
