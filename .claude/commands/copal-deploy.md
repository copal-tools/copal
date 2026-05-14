---
description: Walk through a CopalVX server deploy following copalvx/server/DEPLOY.md. Confirms deploy class, then narrates each step. Read-only — user runs the actual SSH commands.
---

# /copal-deploy

**When to use:** before deploying any change to the CopalVX server. Replaces re-reading `copalvx/server/DEPLOY.md` from scratch.

## Procedure

1. **Read** `copalvx/server/DEPLOY.md` to refresh the four classes.

2. **Ask the user which class** (use AskUserQuestion):
   - **Class A — Routine** (code-only changes, no schema, no compose changes): just `git pull && docker compose build && docker compose up -d`
   - **Class B — Schema migration** (DB schema changed): includes a controlled `init_db.py` run after the build
   - **Class C — Breaking protocol** (API contract change that requires client upgrade): includes client-version coordination
   - **Class D — Clean slate** (storage wipe; only with explicit user OK): includes `--clean-slate` to `init_db.py` and SeaweedFS volume reset

3. **Run preflight checks** (read-only):
   - `git status` — confirm the worktree is clean (or only contains the deploy commit)
   - `git log --oneline -5` — confirm the commit being deployed is at HEAD
   - Read `docker-compose.yml` — confirm storage contract (every stateful container path on a mounted volume; the SeaweedFS filer leveldb specifically must be on `/mnt/FastSSD/seaweed_filer` per Phase 6 lessons)

4. **Narrate the steps for the chosen class.** Don't execute SSH commands — surface them as a checklist for the user to run on the server. Include the rollback path inline (e.g. "if A fails, `docker compose up -d --no-deps asset-hub-api` against the previous image").

5. **Post-deploy verification** the user should run:
   - `docker compose ps` — all containers Up
   - `curl http://<server>:8005/healthz` — returns 200
   - `cd copalvx/client && uv run pytest tests/integration/ -q` — full integration suite passes against live server

## Don't

- Don't actually SSH or run `docker compose down -v` from this slash command — server-side actions are the user's responsibility (they may be on a different machine).
- Don't recommend Class D without explicit user confirmation — it wipes the SeaweedFS volume and is unrecoverable.
