# Lint and Format Python Code

## When to use
After writing or modifying any Python file.

## Steps

### 1. Format with Black
```bash
black --line-length 100 .
```

### 2. Sort imports
```bash
isort --profile black .
```

### 3. Lint with Ruff
```bash
ruff check . --fix
```

### 4. Type check
```bash
mypy --strict .
```

## Success criteria
All four commands exit with code 0. If any fail after auto-fix, 
manually resolve the remaining issues before proceeding.