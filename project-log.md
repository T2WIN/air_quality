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