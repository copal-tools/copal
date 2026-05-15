---
description: Audit the current diff against the documented gotcha catalog (storage contract, HKLM, encoding, FK order, non-fatal hooks, body-size limit, path-traversal). Invokes the copal-gotcha-reviewer subagent.
---

# /copal-gotcha-check

**When to use:** before merging or deploying anything substantial. The documented gotchas are expensive to relearn — this catches them at review time.

## Procedure

1. **Determine the diff scope:**
   - Default: `git diff main...HEAD` + uncommitted changes
   - If user passed a ref, diff against that
   - If diff is empty, surface and stop

2. **Invoke the `copal-gotcha-reviewer` subagent** via the Agent tool. Pass it:
   - The list of changed files (`git diff --name-only main...HEAD`)
   - The instruction: "Review these changes against the documented gotcha catalog. Report findings grouped by severity (must-fix / should-fix / nit) with file:line references. Be specific — quote the offending lines."

3. **Surface the report verbatim** to the user. Do not soften must-fix findings. If the diff is clean, say so in one line.

4. **Offer to fix** any must-fix findings inline (if simple) or open them as TODOs (if cross-cutting).

## Gotcha catalog (the subagent's checklist)

| Gotcha | Where it bites | Quick check |
|---|---|---|
| Storage contract | `copalvx/server/docker-compose.yml` | New container path under `/data/` etc. must be on a mounted volume |
| HKLM/Win11 24H2 | `copalpm/src/copalpm/shell_integration.py` | New shell verbs must use HKLM, not HKCU |
| Subprocess encoding | Both packages, any subprocess.run with output | Must pass `PYTHONIOENCODING=utf-8` env on Windows |
| FK delete order | `copalvx/server/app/main.py` | DELETE must respect projects → commits → project_files → assets cascade |
| Non-fatal subprocess hooks | `copalvx/client/copal_core/pm_hooks.py`, `copalpm/src/copalpm/copalvx_api.py` | Wrap in try/except, warn and continue, never raise |
| Body size limit | `copalvx/server/app/main.py` | New POST endpoints respect or document 50 MB Content-Length cap |
| Path-traversal guard | `copalvx/client/copal_core/sync.py`, `transport.py` | Client writes use `realpath`-based safe-root check |

## Don't

- Don't run product tests as part of this — that's `/copal-test`'s job.
- Don't auto-fix without surfacing the finding first.
