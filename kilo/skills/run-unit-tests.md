# Run Unit Tests

## When to use
After any code change. Must pass before committing.

## Prerequisites
```bash
pip install -r requirements-dev.txt
```

## Steps

### Run the test suite
```bash
pytest tests/unit/ -v --tb=short
```

### With coverage (when requested or before PR)
```bash
pytest tests/unit/ --cov=ingestion --cov=warehouse --cov-report=term-missing --cov-fail-under=80
```

## Success criteria
All tests pass. Coverage meets threshold when measured.

## If tests fail
1. Read the failure output carefully.
2. Determine if the failure is in your new code or existing code.
3. Fix the root cause — do not delete or skip tests to make them pass.