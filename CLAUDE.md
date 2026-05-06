# CLAUDE.md — CopalVX

> This file is for AI assistants. It contains everything needed to understand and
> continue work on CopalVX without reading the full codebase from scratch.
> Last updated: 2026-05-06 (after Phases 1-8 + Parts 1-2 + post-init push complete).

---

## What CopalVX is

CopalVX is a **content-addressable asset management system** for media/VFX pipelines.
It lets an artist push a folder of files to a central server (versioned) and pull
any version back on any machine on the LAN. Think "git for large files" but simpler
and with no branching.

**Core idea:** Files are stored by their SHA-256 hash. Uploading the same bytes
twice is a no-op — the server deduplicates automatically. A "version" (commit) is
just a pointer: this project name + version tag = this list of hashes.

---

## System architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        CLIENT MACHINE                            │
│  tui.py ──► copal_core/api.py ──► FastAPI :8005                 │
│               │                                                  │
│               └──► copal_core/transport.py ──► SeaweedFS :8888  │
│  (pm_hooks.py calls `project` and `pm` CLIs via subprocess only) │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                        SERVER MACHINE                            │
│  Docker Compose:                                                 │
│    asset-hub-api  (FastAPI, port 8005)                           │
│    asset-db       (PostgreSQL 15, internal only)                 │
│    seaweedfs      (SeaweedFS, ports 9333/8080/8888/8333)         │
└──────────────────────────────────────────────────────────────────┘
```

**Key facts:**
- SeaweedFS port 8888 (Filer) is used directly by the client for uploads/downloads
- SeaweedFS port 9333 (Master) is used internally by the API for assign requests
- PostgreSQL is NOT exposed externally — API reaches it via Docker internal network
- The API is the only thing that talks to PostgreSQL

---

## File map

```
E:\Development\Copal-VX\
│
├── CLAUDE.md                          ← You are here
│
├── client/
│   ├── tui.py                         ← Main entry point (TUI + CLI dispatch via argparse)
│   ├── pyproject.toml                 ← uv project config (hatchling build)
│   └── copal_core/
│       ├── config.py                  ← Config loader: ~/.copal/config.json (auto-persists new keys)
│       ├── api.py                     ← HTTP calls to FastAPI server
│       ├── transport.py               ← File upload/download to SeaweedFS
│       ├── fs.py                      ← File scan, SHA-256 hash, .copalignore
│       ├── sync.py                    ← SyncEngine: parallel upload/download planner
│       ├── versioning.py              ← Tag parsing (v1.0 → v1.1, validate, etc.)
│       ├── registry.py                ← Local recent-projects list (~/.copal/projects.json)
│       └── pm_hooks.py                ← ProjectRegistry integration (see below)
│
├── server/
│   ├── docker-compose.yml             ← All three containers
│   ├── .env                           ← SECRETS — never in git, must exist on server
│   ├── .env.example                   ← Template — safe to commit
│   └── app/
│       ├── main.py                    ← FastAPI app: all endpoints
│       ├── database.py                ← SQLAlchemy engine + get_db()
│       └── init_db.py                 ← Schema setup (idempotent, safe to rerun)
│
└── (no legacy scripts remain — deleted in Phase 8)
```

---

## Database schema

```sql
projects
  id          UUID  PK
  name        TEXT  UNIQUE  -- The CopalVX project name (e.g. "MyMovie")
  description TEXT          -- Exists, never populated, reserved for Phase 6
  created_at  TIMESTAMP

commits
  id          UUID  PK
  project_id  UUID  FK → projects.id
  version_tag TEXT          -- e.g. "v1.0", "v2.3"
  message     TEXT
  author_name TEXT
  created_at  TIMESTAMP
  UNIQUE(project_id, version_tag)  -- Added in Phase 4

assets
  id          UUID  PK
  file_hash   TEXT  UNIQUE  -- SHA-256 hex
  size_bytes  BIGINT
  seaweed_fid TEXT          -- SeaweedFS FID (e.g. "/blobs/<hash>")
  mime_type   TEXT
  created_at  TIMESTAMP

project_files
  id          UUID  PK
  commit_id   UUID  FK → commits.id  (CASCADE DELETE)
  asset_id    UUID  FK → assets.id
  file_path   TEXT          -- Relative path within the project folder

Indexes (added Phase 4):
  idx_commits_project_version  ON commits(project_id, version_tag)
  idx_commits_project_created  ON commits(project_id, created_at DESC)
```

---

## Push flow (step by step)

1. **pre-push hook** — `pm_hooks.hook_pre_push(root_dir)` runs `project sync-time` to flush time tracking into project.yaml before scan (non-fatal if pm not installed)
2. **Scan** — `fs.scan_directory()` walks root dir, hashes every file (SHA-256), respects `.copalignore`
3. **Handshake** — POST `/handshake` with full manifest; server returns list of hashes it doesn't already have
4. **Get upload URLs** — POST `/get_upload_urls` for all needed files; server queries SeaweedFS master for FIDs
5. **Upload** — `SyncEngine.execute_upload_plan()` uploads files in parallel (8 threads) via PUT to SeaweedFS filer `:8888`. Uses retry with exponential backoff (1s/2s/4s, max 3 attempts)
6. **Confirm** — POST `/confirm_upload` for each uploaded file to record hash → FID mapping in PostgreSQL
7. **Commit** — POST `/commit` with full file list. Server:
   - Resolves/validates all hashes before writing anything
   - Inserts commit + project_files in single atomic transaction
   - Returns 422 if any hash missing, 409 if version tag already exists
8. **Save state** — `fs.save_local_state()` writes `.copal/state.json` in root dir
9. **Local registry** — `registry.register_project()` updates `~/.copal/projects.json`
10. **post-push hook** — `pm_hooks.hook_post_push()` runs `project copalvx-update` to stamp project.yaml with CopalVX project name and version tag

---

## Pull flow (step by step)

1. **Get versions** — GET `/projects/{name}/versions`
2. **Get manifest** — GET `/checkout/{project}/{tag}` returns list of {path, fid, hash, size}
3. **Generate plan** — `SyncEngine.generate_plan()` compares manifest vs local files; detects moves (same hash different path = LOCAL_COPY, zero bandwidth)
4. **Execute plan** — parallel downloads from SeaweedFS filer, local copies, backups per policy
5. **Save state + registry** — same as push
6. **post-pull hooks** — `pm register <path>` + display CopalVX block from pulled project.yaml

---

## ProjectRegistry integration (pm_hooks)

ProjectRegistry is a **completely separate** file-based project/time-tracking system. It does NOT overlap with CopalVX functionality. Understanding the relationship:

| System | What it stores | Storage |
|--------|---------------|---------|
| CopalVX server | File content (blobs), version history | PostgreSQL + SeaweedFS |
| ProjectRegistry | Project metadata, time logs, notes | project.yaml files + machine registry JSON |

**The link:** A ProjectRegistry project's `project.yaml` has a `copalvx:` block:
```yaml
copalvx:
  project_name: "MyMovie"          # CopalVX project name
  last_push: "2026-05-04T10:30:00" # Timestamp of last push
  last_push_version: "v2.1"        # Version tag of last push
```

This is written by `project copalvx-update` (a ProjectRegistry CLI command) which is called by `pm_hooks.hook_post_push()` after every successful CopalVX push.

**The pm_hooks system is immune to all CopalVX server/API changes** — it only calls external CLIs via subprocess. The hooks are all non-fatal: if `project` or `pm` are not in PATH, a warning prints and CopalVX continues normally.

**Three separate "names" to not confuse:**
1. **ProjectRegistry internal ID** — `PROJ-SLUG-DDMMYY` (auto-generated)
2. **ProjectRegistry name** — human label in the pm UI ("My Feature Film")
3. **CopalVX project_name** — what's in project.yaml's `copalvx.project_name` and what CopalVX uses as the key for versions/commits ("MyFeatureFilm")

They can be different. CopalVX doesn't know about or care about ProjectRegistry IDs.

---

## Client config

Each machine has `~/.copal/config.json` (auto-created on first run):
```json
{
    "server_ip": "192.168.178.161",
    "api_port": 8005,
    "filer_port": 8888,
    "default_author": "simon",
    "default_projects_root": "D:\\Projects",
    "client_path": "E:\\Development\\Copal-VX\\client"
}
```

| Key | Purpose |
|-----|---------|
| `server_ip` | Server address — all API/filer endpoints derive from this |
| `api_port` | FastAPI port (default 8005) |
| `filer_port` | SeaweedFS filer port (default 8888) |
| `default_author` | Used in commits when no author specified |
| `default_projects_root` | Default root for project folders |
| `client_path` | **Required for pm-tui integration.** Absolute path to the CopalVX client directory where `pyproject.toml` lives. pm-tui uses this as `cwd` when running `uv run copalvx push/pull` as a subprocess. |

**Config migration:** `config.py` now auto-persists any new default keys to disk. If a key exists in `DEFAULT_CONFIG` but not in the user's file, it gets written back on next client startup. This prevents future "missing key" issues when new config keys are added.

---

## Server deployment

**Server lives on a separate Linux machine** (or wherever Docker runs).

**Environment:** `server/.env` (not in git — must be created manually):
```env
POSTGRES_USER=admin
POSTGRES_PASSWORD=<strong_password>
POSTGRES_DB=asset_system
SEAWEED_MASTER_URL=http://seaweedfs:9333
WEED_S3_ACCESS_KEY=
WEED_S3_SECRET_KEY=
PUBLIC_ACCESS_HOST=192.168.178.161
LOG_LEVEL=INFO
```

**Deploy workflow:**
```bash
# On dev machine (Windows desktop):
git push

# On server:
git pull
docker-compose up -d --build asset-api   # rebuild API only (DB + SeaweedFS keep running)
```

Full restart (all containers, ~30s downtime):
```bash
docker-compose down && docker-compose up -d
```

**Replication note:** SeaweedFS replication is set to `000` (no replication).
`001` requires 2+ volume server instances — this is a single-machine setup.
Do not change replication to `001` without adding a second volume server.

---

## Completed phases (as of 2026-05-06)

| Phase | What | Files changed |
|-------|------|---------------|
| 1 | Client hardening: timeouts (30s/10s), retry w/ backoff, thread-safe HTTP session, subprocess instead of os.system, proper error propagation | transport.py, api.py, fs.py, tui.py |
| 2 | Protect init_db.py: DROP gated behind `--clean-slate` + "DELETE EVERYTHING" prompt, all CREATE TABLE/INDEX → IF NOT EXISTS | server/app/init_db.py |
| 3 | Server hardening: structured logging, error sanitization, .env secrets, health checks in docker-compose, connection pool config | main.py, database.py, docker-compose.yml, .env.example |
| 4 | DB schema: UNIQUE(project_id, version_tag) constraint + 2 performance indexes via CONCURRENTLY; 409 on duplicate version | main.py (IntegrityError handling), psql ALTER TABLE |
| 5 | Atomic commit: validate ALL hashes before writing, single atomic transaction with full rollback on any failure | main.py /commit endpoint |
| 6 | Explicit project creation: POST /projects (201/409), ensure_project() called before every push, auto-creation removed from /commit; also fixed missing UNIQUE on projects.name and synced init_db.py to match live schema | main.py, api.py, tui.py, init_db.py |
| 8 | Cleanup: deleted legacy scripts (client_connect.py, checkout.py); blob verification in /confirm_upload (HEAD to SeaweedFS filer before DB insert); download re-hash (SHA-256 after write, delete+fail on mismatch); fixed tuple-unpacking bug in sync.py; CLI entry point (`uv run copalvx`) | main.py, transport.py, sync.py, pyproject.toml |
| Part 1 | Server API additions: GET /health, GET /projects (with stats), DELETE /projects/{name} (with orphan cleanup), enhanced /metadata with authors list | main.py |
| Part 2 | pm-tui push/pull integration: argparse CLI dispatch, push_cli/pull_cli functions, progress callbacks in SyncEngine, config auto-persist, subprocess streaming, post-init auto-push | tui.py, config.py + ProjectRegistry copalvx_api.py, tui_app.py |

---

## Current API surface

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/projects` | Create project (201/409) |
| GET | `/projects` | List all projects with stats |
| GET | `/projects/{name}/versions` | List versions (newest first) |
| GET | `/projects/{name}/metadata` | Project detail (includes `authors` list) |
| DELETE | `/projects/{name}` | Delete project (body: `{delete_orphan_files: bool}`) |
| POST | `/handshake` | Compare client manifest with server |
| POST | `/get_upload_urls` | Request SeaweedFS FIDs for upload |
| POST | `/confirm_upload` | Record uploaded blob (with HEAD verification) |
| POST | `/commit` | Create version (atomic, validates all hashes) |
| GET | `/checkout/{name}/{tag}` | Get file manifest for a version |
| GET | `/health` | Service health (DB + SeaweedFS connectivity) |

---

## Next up: TUI redesign (decided 2026-05-06)

**Design decision:** CopalVX TUI becomes a service dashboard. Push/pull moves to pm-tui as the primary interface. CopalVX TUI keeps push/pull as a backup (standalone use without pm).

### Part 2 — pm-tui push/pull integration (COMPLETE)

Added CopalVX push/pull to ProjectRegistry's TUI (`E:\Development\ProjectRegistry`):
- New file: `src/project_registry/copalvx_api.py` — HTTP client + subprocess launcher
- Keybindings `p` (push) and `l` (pull) in `ProjectDetailScreen`
- Push: project path from registry, auto-suggest next version tag via modal input
- Pull: fetch versions from server, user selects from dropdown
- Progress modal (`CopalVXProgressModal`): streams subprocess output line-by-line with a `ProgressBar` and `RichLog`
- CLI mode: `tui.py` has `push_cli()` / `pull_cli()` dispatched via argparse subcommands
- Entry point: `uv run copalvx push <project> <tag> <path> [--message] [--author]`
- Post-init auto-push: creating a new project in pm-tui automatically pushes v1.0 to CopalVX (progress modal shown immediately after project creation)

**Integration pattern:** pm-tui invokes `uv run copalvx push/pull` as a subprocess (cwd from `client_path` in config). This keeps the two repos fully independent — no shared imports, no tight coupling. Progress lines (`[UPLOAD] 3/10 filename`) are parsed by pm-tui to update the progress bar.

**Author handling:** pm-tui passes empty author to the subprocess. `push_cli` falls back to `default_author` from `~/.copal/config.json` (the actual user name), avoiding the earlier mistake of passing the PM project ID as the commit author.

### Part 3 — CopalVX dashboard TUI (NOT STARTED)

Rewrite `client/tui.py` as a simple terminal dashboard:
- System health indicators (API, DB, SeaweedFS)
- Project list table (name, versions, last push, authors)
- Project detail (versions, delete option)
- Push/pull kept as secondary menu items (backup/standalone use)
- Simple terminal style (not Textual), upgradeable later

### Phase 7 — Authentication (LOW PRIORITY)

LAN-only system, not urgent. When needed:
- Stage 1: `X-Copal-Key` middleware in `log_only` mode
- Stage 2: Clients send key from `~/.copal/config.json`
- Stage 3: Flip `AUTH_MODE=enforce` in `.env`
- pm_hooks are unaffected (they don't call the CopalVX API)

---

## Design principles (from MEMORY.md)

> "All tools must prioritize robustness & ease of use over features."

Applied in CopalVX:
- Every network call has timeouts and retries
- Every commit is atomic (all hashes validated before any DB write)
- The TUI always shows a specific error message; never silently loops back to the menu
- pm_hooks are non-fatal by design — CopalVX must work even if ProjectRegistry is absent

---

## How to run the client (Windows)

```powershell
cd E:\Development\Copal-VX\client
uv run copalvx         # recommended (requires uv sync first)
uv run python tui.py   # also works
```

---

## Database maintenance commands

```bash
# Backup
docker exec asset-db pg_dump -U admin asset_system > backup_$(date +%Y%m%d).sql

# Restore
cat backup_YYYYMMDD.sql | docker exec -i asset-db psql -U admin asset_system

# Connect to DB
docker exec -it asset-db psql -U admin asset_system

# Check for duplicate version tags
SELECT p.name, c.version_tag, COUNT(*) FROM commits c
JOIN projects p ON c.project_id = p.id
GROUP BY p.name, c.version_tag HAVING COUNT(*) > 1;

# List all projects
SELECT name, created_at FROM projects ORDER BY created_at DESC;

# Delete a test project (CASCADE removes commits + project_files)
DELETE FROM projects WHERE name = 'TestProjectName';
```

---

## Known gotchas

1. **SeaweedFS replication `001` requires 2+ volume servers.** Single-machine setup must stay at `000`. Error: "No matching data node found!" — fix: revert to `000` and run `docker-compose up -d seaweedfs` (NOT `restart` — `restart` doesn't re-read compose file).

2. **`docker-compose restart` does NOT re-read `docker-compose.yml`.** Use `docker-compose up -d <service>` to pick up config changes.

3. **`init_db.py` is safe to rerun** (all CREATE IF NOT EXISTS, DROP gated). Never run `--clean-slate` on a live system with real data.

4. **get_versions() returns `[]` for genuine 404 (new project), raises ConnectionError for network failures.** This was a bug: previously it swallowed all errors and returned `[]`, making the TUI show "New Project" even when the server was down.

5. **projects.description is populated by POST /projects** (added in Phase 6). Older projects created before Phase 6 have NULL description — safe to ignore.

6. **The client registry (`~/.copal/projects.json`) is separate from ProjectRegistry.** It's just a "recently used" list for the TUI's convenience. Max 20 entries.

7. **`client_path` must be set explicitly in `~/.copal/config.json`.** Auto-detection via `Path(__file__)` is unreliable because `__file__` resolves to the installed site-packages location (e.g. `.venv/Lib/site-packages/copal_core/config.py`), not the source directory. pm-tui reads the raw JSON — it doesn't use client-side config merging.

8. **PowerShell `Set-Content -Encoding utf8` writes UTF-8 with BOM on Windows PowerShell 5.1.** Python's `json.loads()` chokes on the BOM with "Unexpected UTF-8 BOM" error. Fix: use `encoding="utf-8-sig"` when reading, or write with `[System.Text.UTF8Encoding]::new($false)` from PowerShell. `copalvx_api.py` already uses `utf-8-sig`.

9. **Subprocess stdout encoding defaults to cp1252 on Windows when piped.** Emoji in print statements (`pm_hooks.py` uses them) causes `UnicodeEncodeError: 'charmap' codec can't encode character`. Fix: pass `PYTHONIOENCODING=utf-8` in the subprocess environment.

10. **`call_from_thread()` is on `App`, not `Screen`.** In Textual, use `self.app.call_from_thread()` not `self.call_from_thread()` when calling from a background thread inside a Screen subclass.

11. **`PYTHONUNBUFFERED=1` required for real-time subprocess streaming.** Without it, Python buffers stdout in a subprocess, so the parent can't read lines as they're printed. Set it in the subprocess env alongside `PYTHONIOENCODING`.
