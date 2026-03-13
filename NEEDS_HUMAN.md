# NEEDS_HUMAN.md

This file is maintained by Forge. It contains tasks and items that require
human attention before the agent can proceed.

**How to use:**
1. Review each item below
2. Provide answers/decisions in the "Resolution" field
3. Run `forge checkin` to process your responses and unpark tasks
4. Forge will resume automatically after checkin

---


## ~~[64311a4e]~~ RESOLVED GitHub Actions CI workflow
**Added:** 2026-03-13 01:46 UTC
**Reason:** Review the generated workflow to ensure it matches your repository's branch naming conventions (main vs master) and any org-level required checks.

**Task description:**
NEEDS_HUMAN: Review the generated workflow to ensure it matches your repository's branch naming conventions (main vs master) and any org-level required checks.

Create `.github/workflows/ci.yml` for automated testing on push and PR.

```yaml
name: CI

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12']

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e .
          pip install pytest pytest-cov

      - name: Run tests
        run: python -m pytest tests/ -v --tb=short

      - name: Check forge CLI loads
        run: forge --version
```

Also create `.github/workflows/` directory if it doesn't exist.

No test required for this task (it's a CI config). Verify the YAML is syntactically valid by ensuring proper indentation and structure.

**Resolution:** *(fill this in, then run `forge checkin`)*

---
