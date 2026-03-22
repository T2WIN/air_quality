# Agent Instructions — Air Quality Platform

## Project Overview

GCP data platform for air quality monitoring. Stack:
- **Pollers**: Python Cloud Run Jobs (OpenAQ, Open-Meteo) on scheduled loops
- **Storage**: BigQuery — raw → staging (deduped views) → analytics
- **Orchestration**: Cloud Scheduler → Cloud Run Jobs
- **Messaging**: Pub/Sub
- **Dashboard**: Streamlit on Cloud Run
- **Infra management**: gcloud CLI, documented in RUNBOOK.md

Repository structure:
```
ingestion/          # Python poller code
warehouse/          # SQL: raw/, staging/, viz/, tests/
dashboard/          # Streamlit app
tests/              # Python tests: unit/, integration/
docs/adr/           # Architecture Decision Records
scripts/            # Automation scripts
memory/             # Agent memory files
RUNBOOK.md          # Infrastructure setup playbook
CODEBASE_QUALITY.md # Mandatory coding standards
.env.example        # Environment variable reference
```

---

## Hard Rules — Always Active

1. **Production is read-only.** Never create, modify, or delete resources in project
   `air-quality-490517`. Read-only BQ queries for investigation are acceptable.
2. **All infrastructure work happens in the dev project.** If `DEV_PROJECT_ID` is not
   set in `.env`, limit yourself to code changes, local tests, and linting. Tell the
   user a dev project is needed for infrastructure work.
3. **Never deploy without user confirmation.** Present a plan, get explicit approval.
4. **Never commit secrets.** No API keys, passwords, or credentials in code, commits,
   or logs. Secrets live in Secret Manager, referenced by name only.
5. **Never skip the quality checklist.** Every code change must be verified against
   `CODEBASE_QUALITY.md` before presenting to the user.
6. **Never ignore test failures.** Fix them before proceeding.
7. **Always update memory** at the end of every session.
8. **Components that do similar things must have similar code.** When building something
   that parallels an existing component, match its structure and patterns.

---

## Task Workflow — Every Task Follows These Phases

### Phase 1: Orient
1. Read the task description. If ambiguous, ask questions before writing code.
2. Read `memory/current-feature.md` for ongoing context.
3. Read files directly related to the task.
4. Identify which files will be affected.

### Phase 2: Plan
For any task touching more than 2 files or involving infrastructure:
1. State what you will change and why.
2. State what tests or validations you will run.
3. State what risks exist.
4. Wait for user approval before proceeding.

For simple, contained changes (single function, formatting fix), proceed directly.

### Phase 3: Execute
Follow the appropriate protocol below:
- Writing Python → **Code Quality Protocol**
- Writing SQL views → **SQL Development Protocol**
- Modifying infrastructure → **Runbook Protocol**

### Phase 4: Verify
Run all applicable checks. Do not present unverified work.

### Phase 5: Document
1. Update `memory/current-feature.md` with what was done, decisions made, and next steps.
2. If a feature is complete, move the memory to `memory/completed/` and append a
   summary to `memory/project-log.md`.
3. Make a clean git commit with an imperative-mood message.

---

## Protocol: Code Quality

When writing or modifying Python code:

### Step 1 — Write the code
Follow existing patterns. New pollers match existing poller structure. New modules
match the conventions of their siblings.

### Step 2 — Run automated checks
Use the `lint-and-format` skill, then the `run-unit-tests` skill.
All must pass before proceeding.

### Step 3 — Verify against CODEBASE_QUALITY.md
Open `CODEBASE_QUALITY.md` and check your changes against every applicable section.
Produce an explicit checklist:

```
## Quality Verification
- [x] G1 Readability: functions are short, names are descriptive
- [x] G2 Explicit: config parsed via Config dataclass, no hidden defaults
- [x] G4 Idempotency: dedup_key prevents duplicate rows on re-run
- [x] 2.1 Formatting: black/isort/ruff pass
- [x] 2.3 Type hints: all signatures annotated
- [x] 2.4 Config: single frozen dataclass, parsed once in main()
- [x] 2.5 Error handling: transient errors retried, permanent errors fail loud
- [x] 2.6 Logging: structured key=value, no print(), no secrets
- [x] 2.7 Function design: all under 40 lines, pure where possible
- [ ] 2.8 Concurrency: N/A — single-threaded module
- [x] 5.1 Tests: test file created with unit tests for all public functions
```

Do not skip sections — mark them N/A with a reason if they don't apply.
If anything fails, fix it before continuing.

### Step 4 — Update tests
- Modified logic → update existing tests.
- New functions/modules → create test files following pattern `tests/unit/test_<module>.py`.
- Run the test suite again.

### Step 5 — Update docs
- Changed infrastructure → update `RUNBOOK.md`
- Changed env vars → update `.env.example`  
- Significant design decision → create ADR in `docs/adr/NNNN-title.md`

---

## Protocol: SQL Development

When creating or modifying BigQuery views or tables:

### Step 1 — Define acceptance criteria FIRST
Before writing SQL, define properties the result must satisfy. Present to user:

```
## View: openaq_hourly_latest
### Acceptance Criteria:
1. No duplicate dedup_keys
2. For each dedup_key, only the latest ingested_at row is kept
3. No NULLs in: station_id, period_from_utc, parameter
4. Row count ≤ source table row count
```

Wait for user approval of criteria before writing the view.

### Step 2 — Write validation queries
For each criterion, write a SQL query that returns 0 rows on success:

```sql
-- Assert: No duplicate dedup_keys
SELECT dedup_key, COUNT(*) AS cnt
FROM `{project}.{dataset}.{view}`
GROUP BY dedup_key
HAVING cnt > 1;
```

Save in `warehouse/tests/test_<object_name>.sql`.

### Step 3 — Write the view/table SQL
Place in the appropriate `warehouse/` subdirectory.
Follow SQL standards from `CODEBASE_QUALITY.md` §3.

### Step 4 — Deploy to dev and validate
1. Use `bq-query` skill to deploy to the dev project.
2. Run all validation queries from Step 2.
3. All must return 0 rows. Report results explicitly.

---

## Protocol: Runbook Changes

When modifying infrastructure:

### Step 1 — Make the change
Modify or add `gcloud`/`bq` commands as needed.

### Step 2 — Update RUNBOOK.md in the same session
Ensure the runbook reflects the change. Follow runbook quality rules:
- All commands use `.env` variables — no hardcoded project IDs, regions, or names.
- All create commands are idempotent (`|| true`, `IF NOT EXISTS`, `CREATE OR REPLACE`).
- Steps are in dependency order.
- Each step has a clear heading.

### Step 3 — Validate end-to-end in dev
1. Run `teardown-dev` skill to clean-slate the dev project.
2. Run `run-runbook` skill against the dev project.
3. Run `validate-infra` skill to assert all resources exist.
4. Report any failures.

If the dev project is not configured, note in `memory/current-feature.md` that
runbook validation is pending and alert the user.

---

## Memory Management

### Short-term: `memory/current-feature.md`
Updated at the end of **every session**. Overwritten each time (it tracks the active feature).

```markdown
# Feature: [name]
## Status: [in-progress | blocked | complete]
## Last updated: [YYYY-MM-DD]

### What was done this session
- [concrete changes made]

### Decisions made
- [decision]: [rationale]

### Open questions / blockers
- [unresolved items]

### Next steps
- [ ] [specific next actions]

### Files modified
- [file paths]
```

When a feature is complete:
1. Move file to `memory/completed/YYYY-MM-DD-feature-name.md`
2. Append summary to `memory/project-log.md`

### Long-term: `memory/project-log.md`
Append-only. One entry per significant event.

```markdown
## YYYY-MM-DD — [one-line summary]
- **What**: [description]
- **Why**: [context/motivation]
- **Impact**: [what this changes going forward]
- **Key files**: [paths]
```

### Reading memory
At the start of every session:
1. Read `memory/current-feature.md` if it exists.
2. If starting a new feature, skim `memory/project-log.md` for relevant context.

---

## Git Protocol

- **Commit messages**: Imperative mood, ≤72 char subject. Body explains *why*.
  `Add dedup validation queries for weather staging view`
- **One logical change per commit.** Don't bundle unrelated changes.
- **Branch per feature**: `feature/add-pm10-view`, `fix/dedup-key-collision`
- **Run lint + tests before every commit.**
- **Never commit `.env`**, secrets, or large data files.

---

## Skills Reference

Use these skills by following the procedures in the skills folder:

| Skill | Purpose | When |
|-------|---------|------|
| `lint-and-format` | Run black, isort, ruff, mypy | After any Python change |
| `run-unit-tests` | Run pytest on unit tests | After any code change |
| `bq-query` | Execute SQL against BigQuery | SQL development, investigation |
| `validate-infra` | Assert GCP resources exist correctly | After runbook changes |
| `run-runbook` | Execute full RUNBOOK.md against dev project | Setting up dev from scratch |
| `teardown-dev` | Delete all resources in dev project | Before run-runbook |
| `check-code-quality` | Full CODEBASE_QUALITY.md review | Before presenting any code |
| `validate-sql-tests` | Run SQL assertion queries | After deploying views |

---

## Notes on Current State

These are known gaps between the codebase and the quality standards.
Work toward closing them incrementally — don't try to fix everything at once.

- **No CI/CD pipeline exists yet.** Run checks locally via skills.
- **No Python tests exist yet.** Create them alongside any code changes.
- **No dev GCP project yet.** Infrastructure work is blocked until one is created.
- **RUNBOOK.md has ordering/idempotency issues.** See the runbook protocol for the list.
  Fix these when you next touch the runbook.
- **SQL validation queries don't exist yet.** Create them alongside any view changes.