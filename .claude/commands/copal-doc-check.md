---
description: Check whether code changes warrant CLAUDE.md updates and draft the patch. Invokes the copal-doc-curator subagent.
---

# /copal-doc-check

**When to use:** after making code changes, before committing. Catches the most common drift point — code shipped but the relevant CLAUDE.md not updated.

## Procedure

1. **Determine the diff scope:**
   - Default: uncommitted changes + commits since main
   - `git diff --name-only main...HEAD` + `git status --porcelain`
   - If diff is empty, surface and stop

2. **Invoke the `copal-doc-curator` subagent** via the Agent tool. Pass it:
   - The diff
   - The umbrella `CLAUDE.md` "Where does each fact live?" decision tree (it should already know it from its system prompt)
   - The instruction: "Determine which CLAUDE.md files (umbrella, copalvx, copalpm) need updates. For each, draft the minimal patch. Distinguish: must-update (new gotcha, removed module, schema change), should-update (new module, new public API), nit (cosmetic)."

3. **Surface the recommendations.** For each suggested edit:
   - Show the file path and the proposed before/after
   - Mark must-update findings as blocking the commit
   - Ask the user to approve before applying (the curator has Edit access, but should not auto-apply blocking changes silently)

4. **Apply approved edits** via Edit tool calls.

## What the curator looks for

| Code change | Doc impact |
|---|---|
| New module / new file | Add to per-package CLAUDE.md "Source layout" |
| Removed module / file | Delete the corresponding mention |
| New public API endpoint | Add to per-package CLAUDE.md API section |
| Changed DB schema | Update per-package CLAUDE.md schema reference |
| New gotcha (e.g. workaround for OS bug) | Add to "Critical Gotchas" |
| Resolved gotcha | Move from "Critical Gotchas" to "Recently fixed" or remove |
| New CLI subcommand | Add to per-package CLAUDE.md CLI surface |
| Cross-package contract change | Update both packages' CLAUDE.md AND umbrella |
| Phase progress | Update umbrella Status table |

## Don't

- Don't update CLAUDE.md if no code changed — that's not drift, that's just editing.
- Don't write personal preferences into CLAUDE.md — those go to user-global memory.
- Don't auto-apply must-update edits without user confirmation.
