# CopalVX Server — Deployment Runbook

> Audience: anyone deploying or maintaining the CopalVX server on the LAN box.
> If you're about to run `git pull && docker compose ...` and you don't have this
> doc open, stop and open it. Cheaper than a recovery.

---

## Storage contract (READ FIRST)

Every directory inside a container that holds state MUST be on a host-mounted
volume. Anything in a container's writable layer is **lost on container
recreation** — including normal `docker compose down/up` cycles when the
compose project name changes, or `docker compose pull` of a new image.

Current mounts (must match [docker-compose.yml](./docker-compose.yml)):

| State | Container path | Host path | Why |
|---|---|---|---|
| Postgres DB | `/var/lib/postgresql/data` | `./postgres_data` (relative to compose dir) | All API metadata |
| SeaweedFS volume data (`.dat`/`.idx`) | `/data/ssd` | `/mnt/FastSSD/seaweed_data` | Blob bytes (hot tier) |
| SeaweedFS volume data (`.dat`/`.idx`) | `/data/hdd` | `/mnt/pool/SlowHDD/seaweed_data` | Blob bytes (cold tier) |
| **SeaweedFS filer leveldb** | **`/data/filerldb2`** | **`/mnt/FastSSD/seaweed_filer`** | **Path → fid mapping. If lost, blobs become unreachable even though bytes survive. Lost once on 2026-05-13.** |
| Secrets | (file) `.env` | repo root, gitignored | Postgres password, public IP |

**Audit rule when adding features:** any new container path that holds state
(SSE-S3 keys, filer logs, audit dirs, etc.) must land on a mounted volume.
Verify by running `docker compose down && up -d` and confirming the system
still works.

---

## Pre-flight (every deploy)

```bash
ssh server
cd /opt/copal/copalvx/server          # canonical location
docker compose ps                      # all containers Up/healthy
git status                             # clean (changes to .env are gitignored)
git fetch origin
git log HEAD..origin/main --oneline    # preview what's about to land — sanity check
```

Decide which deploy class you're doing (sections below). When in doubt, the
new commits' messages will say.

---

## Class A — Routine deploy (code-only, no schema or protocol changes)

Use when the commits only touch API logic, client code, or docs.

```bash
git pull --ff-only origin main
docker compose build asset-api
docker compose up -d --no-deps --force-recreate asset-api
docker compose logs --tail=20 asset-api      # confirm "Uvicorn running" with no tracebacks
curl -s http://localhost:8005/health         # should print {"healthy":true,...}
```

Downtime: ~5–15 seconds while the container is recreated.

---

## Class B — Schema-change deploy

Use when commits add columns, tables, or indexes to the Postgres schema. The
existing `init_db.py` uses `CREATE TABLE / INDEX IF NOT EXISTS` so it's safe
to re-run; new tables/indexes get added, existing data is untouched.

**Order matters:** apply schema BEFORE replacing the API. Old API on new
schema is harmless; new API on old schema may 500.

```bash
git pull --ff-only origin main
docker compose build asset-api
# Apply schema via a one-shot container built from the new image —
# critically, this uses the NEW init_db.py, not whatever's running.
docker compose run --rm --no-deps asset-api python init_db.py
docker compose up -d --no-deps --force-recreate asset-api
docker compose logs --tail=20 asset-api
# Verify schema landed
docker compose exec db psql -U "$(grep POSTGRES_USER .env|cut -d= -f2)" \
                            -d "$(grep POSTGRES_DB   .env|cut -d= -f2)" -c "\dt"
```

Verify the new column/table is queryable on a real project before declaring
done. Example for the F1 events table:

```bash
PROJ=<existing-project>
curl -s "http://localhost:8005/projects/$PROJ/events?limit=5"   # expect [] or events
```

---

## Class C — Breaking protocol deploy

Use when the server now requires fields/headers/endpoints that old clients
don't send. Examples: F1 added `X-Copal-User` / `X-Copal-Host` requirements
on `/checkout` and `/commit`.

Old clients will receive 4xx until they upgrade.

1. **Announce** the upgrade window to anyone who pushes/pulls regularly.
2. **Deploy the server** using Class A or B above, depending on schema.
3. **Coordinate client upgrades** —
   ```powershell
   uv tool install --reinstall E:\Development\copal\copalvx\client
   ```
4. **Verify with a real client** by doing a push + pull end-to-end before
   declaring the upgrade complete.
5. **Document the breakage in CLAUDE.md** so future readers know which
   client/server versions are compatible.

---

## Class D — Clean-slate deploy (NUCLEAR)

Destroys all project data — bytes, metadata, filer mappings, registry.
Use only when data is acceptably losable (initial setup, dev/staging, or a
deliberate reset like 2026-05-13).

```bash
cd /opt/copal/copalvx/server
docker compose down                                          # stop containers (preserves mounts)

# Wipe Postgres data
sudo rm -rf ./postgres_data
mkdir ./postgres_data                                        # Postgres will reinit on next up

# Wipe SeaweedFS volume blobs
sudo rm -rf /mnt/FastSSD/seaweed_data/*  /mnt/pool/SlowHDD/seaweed_data/*

# Wipe filer leveldb (the mapping that points at the now-gone blobs)
sudo rm -rf /mnt/FastSSD/seaweed_filer/*
sudo mkdir -p /mnt/FastSSD/seaweed_filer

# Bring up the clean stack
docker compose up -d --build
docker compose logs --tail=10 asset-api seaweedfs

# Initialize schema (clean DB has no tables yet)
docker compose run --rm --no-deps asset-api python init_db.py

# Sanity
curl -s http://localhost:8005/projects     # should return []
curl -s http://localhost:8005/health
```

Now have a client push a small test project and pull it back. End-to-end
verification of clean state.

**After a clean-slate, also clean each client's local state.** Otherwise
clients hold stale registry entries pointing at projects that no longer
exist on the server:

```powershell
# On each developer machine
Remove-Item "$env:USERPROFILE\.copal\projects.json" -ErrorAction Continue
# Optional: remove .copal/state.json from each project folder for a fresh
# "first-time pull" experience.
```

---

## Verification (every deploy)

Run these regardless of deploy class. Stop and investigate if any fails.

```bash
docker compose ps                         # all Up/healthy
curl -s http://localhost:8005/health      # {"healthy":true, "services":{...all true...}}
docker compose logs --tail=50 asset-api | grep -iE "error|exception|traceback"   # empty
```

For a real end-to-end check: have a client do a push (creates new bytes) +
pull (round-trips through filer) + manifest fetch (queries DB).

---

## Rollback

```bash
# Find the previous commit (the SHA right before the broken deploy)
git reflog | head -5

# Revert the working tree to that commit
git reset --hard <previous-sha>

# Rebuild and replace the API
docker compose build asset-api
docker compose up -d --no-deps --force-recreate asset-api
```

**Schema rollback** is not automatic. `init_db.py` only adds; it does not
drop. If the broken deploy added a column or table and you need it gone,
write the `ALTER TABLE` / `DROP TABLE` yourself and run it via
`docker compose exec db psql ...`. Don't reach for `--clean-slate` — that
wipes all data, not just the new schema.

If filer or volume data is the issue, see "Lessons learned" below — there
is no in-place rollback for either; restore from backup or re-push.

---

## Backups (TODO — currently nothing automated)

What needs backing up:

| What | Host path | Rough size | Frequency |
|---|---|---|---|
| Postgres data | `./postgres_data` | < 100 MB | Daily |
| Filer leveldb | `/mnt/FastSSD/seaweed_filer` | MB–GB scale (grows with project count) | Daily |
| Volume blobs | `/mnt/FastSSD/seaweed_data` + `/mnt/pool/SlowHDD/seaweed_data` | TB scale | Weekly or none (re-pushable from clients) |
| `.env` | repo root | tiny | On change |

Recommended starting point: a nightly `rsync` (or `tar`-then-rsync) of
`postgres_data` and `seaweed_filer` to a separate disk or off-host location.
The blob data is large but content-addressed — re-pushable from any client
holding a local copy of the project.

---

## Lessons learned

### 2026-05-13 — SeaweedFS filer wipe

While migrating the stack from the pre-rebrand `/opt/Copal-VX/` checkout to
the monorepo at `/opt/copal/copalvx/server/`, `docker compose down` + new
`docker compose up` recreated the seaweedfs container. The filer's leveldb
lived at `/data/filerldb2` — which **was not mounted**. The new container
started with an empty leveldb. Result: every blob path (`/blobs/<sha256>`)
404'd even though the underlying blob bytes still existed on the mounted
`/data/ssd` and `/data/hdd`. Recovery would have required walking volume
`.dat` files and rebuilding the path index. We chose a clean-slate reset
since the data was test material.

**Fix landed in the same commit as this runbook:** `/data/filerldb2` is
now mounted to `/mnt/FastSSD/seaweed_filer`. Audit any future SeaweedFS
configuration changes for similar unmounted-state directories before
committing them.

### 2026-05-13 — Wrong-repo deploy

Pulled new code on the server but ran `docker compose build` from
`/opt/Copal-VX/server/` — the pre-rebrand standalone repo, which doesn't
receive monorepo pushes. The build succeeded but baked in the wrong
`main.py`. Symptom: `grep -n new_function main.py` inside the container
returned empty even after a `--no-cache` rebuild.

**Fix:** server now lives at `/opt/copal/copalvx/server/`. Verify with
`pwd` before running any `docker compose` command.

### 2026-05-13 — Unicode crash on piped stdout

Pulls from PowerShell crashed mid-download with `UnicodeEncodeError` on
`❌` / `✅` emoji because Windows defaults piped stdout to cp1252. The
`copalvx` binary now calls `sys.stdout.reconfigure(encoding="utf-8")` at
the top of `main()`. Affected the client only; recorded here so future
client features that print non-ASCII don't regress this.

---

## Quick reference: which class is this deploy?

- Only `*.py` / `*.md` / client-only changes touched, no SQL, no headers → **A**
- New `CREATE TABLE` / column / index in `init_db.py` → **B**
- New required header, new endpoint that old clients depend on, response shape change → **C**
- Reset from scratch / fresh staging → **D**

When unsure, B is the safer choice (extra `init_db.py` run is harmless).
