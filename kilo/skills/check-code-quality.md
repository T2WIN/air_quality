# Full Code Quality Review

## When to use
As the final step before presenting any code change to the user.

## Steps

### 1. Run automated checks
Execute the `lint-and-format` skill and `run-unit-tests` skill. Both must pass.

### 2. Open CODEBASE_QUALITY.md
Read the file `CODEBASE_QUALITY.md`.

### 3. Review every section
For each section in the document, evaluate whether it applies to your changes.
Produce an explicit checklist:

```
## Code Quality Verification for: [describe the change]

### General Principles
- [x/na] G1 Readability: [one-line justification]
- [x/na] G2 Explicit over implicit: [justification]
- [x/na] G3 Fail loudly: [justification]
- [x/na] G4 Idempotency: [justification]
- [x/na] G5 Least privilege: [justification]

### Python (if applicable)
- [x/na] 2.0 Consistent structure with siblings: [justification]
- [x/na] 2.1 Formatting (verified by lint pass)
- [x/na] 2.2 Naming conventions: [justification]
- [x/na] 2.3 Type hints: [justification]
- [x/na] 2.4 Config management: [justification]
- [x/na] 2.5 Error handling: [justification]
- [x/na] 2.6 Logging: [justification]
- [x/na] 2.7 Function design: [justification]
- [x/na] 2.8 Concurrency: [justification]
- [x/na] 2.9 Dependencies: [justification]

### SQL (if applicable)
- [x/na] 3.1 Formatting: [justification]
- [x/na] 3.2 Naming: [justification]
- [x/na] 3.3 Schema discipline: [justification]
- [x/na] 3.4 File organisation: [justification]

### Infrastructure (if applicable)
- [x/na] 4.1 Env files: [justification]
- [x/na] 4.2 Dockerfiles: [justification]
- [x/na] 4.3 Infra scripts idempotent: [justification]

### Testing
- [x/na] 5.1 Test file exists for modified modules: [justification]
- [x/na] 5.2 Test organisation: [justification]
- [x/na] 5.3 Test naming: [justification]

### Other
- [x/na] 6.1 Git conventions: [justification]
- [x/na] 7 Security: [justification]
- [x/na] 8 Documentation updated: [justification]
```

### 4. Fix violations
If any item fails, fix it. Do not present code with known violations.

### 5. Present the checklist
Include the completed checklist when showing the code to the user.