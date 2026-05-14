---
description: Verify the CopalVX ↔ CopalPM subprocess contract is consistent in both directions. Invokes the copal-cross-package subagent.
---

# /copal-cross-package

**When to use:** when changing either side of the CopalVX ↔ CopalPM subprocess coupling. Trigger files: `copalvx/client/copal_core/pm_hooks.py`, `copalpm/src/copalpm/copalvx_api.py`, or any CLI subcommand on either side that the other calls.

## Procedure

1. **Confirm scope:** quick `git diff --name-only main...HEAD` to verify the diff actually touches the contract surface. If not, surface that and ask the user whether to proceed anyway.

2. **Invoke the `copal-cross-package` subagent** via the Agent tool. Pass it:
   - The diff
   - The instruction: "Diff both sides of the subprocess contract. Verify (a) every `subprocess.run(['copalpm', ...])` call matches a real CLI subcommand in `copalpm/src/copalpm/cli.py`, (b) every `copalvx_api.py` call matches a real `copalvx` subcommand, (c) argument names/shapes haven't drifted, (d) all calls remain non-fatal (try/except wrapped, never raise across packages), (e) both packages' CLAUDE.md describe the contract identically. Report drift with file:line refs and suggest the minimal patch."

3. **Surface the report.** For any drift:
   - Suggest the patch inline
   - Remind the user that contract changes must update both packages' CLAUDE.md in the same commit (per WORKFLOW.md §3)

## Don't

- Don't fix contract drift one-sided — that just moves the bug.
- Don't merge contract changes without running this check.
