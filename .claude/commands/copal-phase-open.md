---
description: Open a new development phase. Args -- <num> <slug>. Updates CLAUDE.md status, MERGE_PLAN.md, project_status memory, cuts a working branch.
---

# /copal-phase-open

**When to use:** starting work on a new numbered phase (Phase 5, Phase 7, etc.). One-time ceremony per phase.

## Args

- `<num>` — phase number (integer)
- `<slug>` — short kebab-case identifier (e.g. `launch-readiness`, `auth`)

If args are missing, ask the user via AskUserQuestion before proceeding.

## Procedure

1. **Confirm with the user:**
   - Phase title (1-line description, e.g. "Public launch readiness — CI, PyPI, repo archival")
   - Goals (3–5 bullets)
   - Owner (default: user)

2. **Edit umbrella `CLAUDE.md`:**
   - Locate the Status table.
   - Add a new row: `| Phase N (<title>) | 🚧 In progress |`
   - If the phase already exists with status ⏳ or ✅, surface the conflict before overwriting.

3. **Edit `MERGE_PLAN.md`:**
   - Append a section:
     ```markdown
     ## Phase N — <title> (opened YYYY-MM-DD)

     **Goals:**
     - bullet 1
     - bullet 2

     **Status:** 🚧 In progress
     ```
   - If `ROADMAP.md` exists (post-Phase-5), use that instead of MERGE_PLAN.md.

4. **Update `project_status.md` memory** at `C:\Users\Sifdone\.claude\projects\E--Development-copal\memory\project_status.md`:
   - Update the body to mention the new active phase + branch + opened date.

5. **Cut the branch** (in the current worktree):
   ```powershell
   git switch -c phase-<N>-<slug>
   ```
   Confirm with the user before running — `git switch -c` is destructive if the branch already exists. If the user is in a worktree, surface that before switching.

6. **Bump the "Last updated" line** in umbrella CLAUDE.md to today's date.

## Don't

- Don't create the branch if there are uncommitted changes — surface and stop.
- Don't open a phase whose number conflicts with an existing one without explicit user confirmation.
