---
description: Run pytest for the affected package(s), auto-detecting from git diff. Args -- --unit-only | --integration | --all | <package>.
---

# /copal-test

**When to use:** after making code changes, before committing. Replaces having to remember per-package `uv` invocations.

## Args (optional)

- `--unit-only` (default) — skip integration tests
- `--integration` — also run integration tests (CopalVX integration tests need the live server up)
- `--all` — both packages, regardless of diff
- `copalvx` | `copalpm` — force a specific package
- (no args) — auto-detect from `git diff --name-only main...HEAD` and `git status --porcelain`

## Procedure

1. **Determine targets:**
   - If a package name was passed → use that.
   - If `--all` → both packages.
   - Otherwise: `git diff --name-only main...HEAD` + `git status --porcelain` → if any path matches `copalpm/**` add CopalPM; if any matches `copalvx/**` add CopalVX. If neither matched, default to running both.

2. **Run for each target package:**

   **CopalPM:**
   ```powershell
   uv run --directory copalpm pytest -q
   ```
   For `--integration`:
   ```powershell
   uv run --directory copalpm pytest -q
   ```
   (CopalPM has no separate integration suite; `tests/integration/` is auto-included.)

   **CopalVX:**
   ```powershell
   cd copalvx/client; uv run pytest -q tests/unit
   ```
   For `--integration`:
   ```powershell
   cd copalvx/client; uv run pytest -q
   ```
   (Integration tests in `tests/integration/` auto-skip if the server is unreachable.)

3. **Report:**
   - Per-package pass/fail counts and time
   - First few failures (if any) with file:line
   - If integration tests were skipped because the server was down, surface that explicitly so the user knows the suite was partial

## Don't

- Don't run `--integration` on CopalVX without first confirming the server is up (`docker compose ps` on the server host) — silent skips give false confidence.
- Don't change directories using `cd` outside of a single Bash command — keep `cd` and `pytest` chained.
