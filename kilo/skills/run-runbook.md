# Run Full Runbook Against Dev Project

## When to use
Setting up the dev environment from scratch, or validating that RUNBOOK.md works.

## Prerequisites
- Dev project must exist and be configured in `.env` as `DEV_PROJECT_ID`
- You should run `teardown-dev` first for a clean-slate test
- User must confirm before execution

## Steps

### 1. Load environment (override project to dev)
```bash
set -a && source .env && set +a
export PROJECT_ID="$DEV_PROJECT_ID"
export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
```

### 2. Execute RUNBOOK.md
Run each step from RUNBOOK.md sequentially, starting from Step 1.
Skip Step 0 (prerequisites/auth) — assume already authenticated.

Execute each code block in order. After each step:
- If it succeeds, continue.
- If it fails, stop and report which step failed and why.

### 3. Validate
Run the `validate-infra` skill to confirm everything was created.

## Rules
- **Never run this against the production project.**
- Always set `PROJECT_ID` to `DEV_PROJECT_ID` before starting.
- Report full output of any failed command.