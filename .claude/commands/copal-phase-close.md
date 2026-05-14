---
description: Close the current development phase. Updates status to complete, writes Shipped block, prunes follow-ups, runs final doc drift sweep.
---

# /copal-phase-close

**When to use:** all goals for a phase are met and the work is merged/ready to merge.

## Procedure

1. **Identify the current phase:**
   - Read umbrella `CLAUDE.md` Status table — find the row marked `🚧 In progress`.
   - Read the corresponding `## Phase N — ...` section in `MERGE_PLAN.md` for the goal list.
   - Confirm with the user that this is the phase being closed.

2. **Build the "Shipped" block** (mirror Phase 6 style in umbrella CLAUDE.md):
   - For each goal: a `**code — short name (date).**` bullet with 1–3 lines describing what landed.
   - Include any bundled fixes / hardening that wasn't in the original goal list.
   - End with a paragraph linking to deeper detail (per-package CLAUDE.md sections, DEPLOY.md, etc.) if relevant.

3. **Edit umbrella `CLAUDE.md`:**
   - Flip the phase row from `🚧 In progress` to `✅ Complete`.
   - Insert the Shipped block under the Status table (above the "Tracked follow-ups" line).
   - Bump "Last updated" line to today.

4. **Edit `MERGE_PLAN.md` / `ROADMAP.md`:**
   - Mark the phase section `**Status:** ✅ Complete (closed YYYY-MM-DD)`.
   - Move "Tracked follow-ups" entries that were completed this phase into a "Done" subsection.
   - Promote any new follow-ups discovered during the phase into the open list.

5. **Update per-package CLAUDE.md** for any:
   - New gotcha discovered in this phase
   - New module / new public API
   - Removed module / removed pattern (delete the corresponding mention)

6. **Update `project_status.md` memory** to reflect closed status + remaining open phases.

7. **Final drift sweep:** invoke `/copal-doc-check` to catch anything missed by step 5.

8. **Suggest** (don't run) the closing commit:
   ```
   docs: close phase N — <title>
   ```

## Don't

- Don't close a phase with `🚧` follow-ups still open without surfacing them and asking what to do.
- Don't delete `MERGE_PLAN.md` content — it's append-only history. Only update statuses.
- Don't skip step 7 — phase-close is the most common drift point.
