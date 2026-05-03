# Dev Tools Setup

## Prerequisites

- Python 3.11+ with `venv` available
- Go (only needed if `gitleaks` is not already installed — it's a Go binary)
- The project virtual environment at `.venv/`

## Installation

### 1. Activate the virtual environment

```bash
source .venv/bin/activate
```

### 2. Install pip packages

All Python tools are pinned in `requirements-dev.txt`:

```bash
pip install -r requirements-dev.txt
```

This installs:

| Tool | Pinned version | Purpose |
|------|---------------|---------|
| `ruff` | 0.15.12 | Formatter + linter |
| `mypy` | 1.20.2 | Static type checker |
| `sqlfluff` | 4.1.0 | SQL linter |
| `pip-audit` | 2.10.0 | CVE scanner for Python deps |
| `pytest` | 9.0.3 | Test runner |
| `pytest-cov` | 7.1.0 | Coverage plugin for pytest |
| `coverage` | 7.13.5 | Coverage measurement (dep of pytest-cov) |

### 3. Install gitleaks (if not already present)

```bash
# Check if installed
gitleaks version

# If missing, install via Go:
go install github.com/gitleaks/gitleaks/v8@latest

# Or download a prebuilt binary from:
# https://github.com/gitleaks/gitleaks/releases
```

Current version used: **8.24.3**

## Verification

After installation, confirm everything is available:

```bash
ruff --version
mypy --version
sqlfluff --version
pip-audit --version
pytest --version
gitleaks version
```

## CI pinning

`requirements-dev.txt` is the single source of truth for CI. Install the exact same versions in CI workflows with:

```bash
pip install -r requirements-dev.txt
```
