# CLAUDE.md — CopalVX

> AI-assistant orientation for the CopalVX package.
> For monorepo-wide context see [../CLAUDE.md](../CLAUDE.md).
> For CopalPM integration details see [../copalpm/CLAUDE.md](../copalpm/CLAUDE.md).
> Last updated: 2026-05-13 (after Phase 4 monorepo restructure).

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
E:\Development\copal\copalvx\         (sits under the copal-tools monorepo)
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
│   └── tests/
│       ├── unit/
│       │   ├── test_sync.py           ← C1 path-traversal guard (6 tests)
│       │   ├── test_transport.py      ← M5 hash-mismatch retry (5 tests)
│       │   └── test_versioning.py     ← ensure_prefix / increment_tag / validate_push_tag (16 tests)
│       └── integration/
│           └── test_server.py         ← Live API tests; auto-skipped if server unreachable (21 tests)
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
  description TEXT          -- Project notes; editable via PATCH /projects/{name}/description
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

Interactive mode (`do_push()`) and CLI mode (`push_cli`) share the same upload/commit logic but differ in ordering:

**Interactive (TUI):**
1. **pre-push hook** — `pm_hooks.hook_pre_push(root_dir)` flushes time tracking (non-fatal)
2. **Scan** — `fs.scan_directory()` walks root dir, hashes every file (SHA-256), respects `.copalignore`
3. **Handshake** — POST `/handshake`; server returns list of hashes it doesn't have
4. **Preview** — shows "N new files (X MB) / Y unchanged" and asks for confirmation before proceeding
5. **Version tag + message** — only prompted after user confirms; new projects default `v1.0`, existing suggest next tag
6. **ensure_project** — POST `/projects` (201 or 409-OK)
7. **Get upload URLs** — POST `/get_upload_urls`; server queries SeaweedFS master for FIDs
8. **Upload** — `SyncEngine.execute_upload_plan()` parallel (8 threads), PUT to SeaweedFS `:8888`, retry w/ backoff
9. **Confirm** — POST `/confirm_upload` for each blob; server does HEAD verify before DB insert
10. **Commit** — POST `/commit`; server validates all hashes, atomic transaction (422 on missing, 409 on dupe tag)
11. **Save state** — `fs.save_local_state()` writes `.copal/state.json`
12. **Local registry** — `registry.register_project()` updates `~/.copal/projects.json`
13. **post-push hook** — `pm_hooks.hook_post_push()` stamps project.yaml with version tag

**CLI (`push_cli`):** No preview step — all params supplied by caller. Steps 2→9→10→11→12→13.

---

## Pull flow (step by step)

1. **Get versions** — GET `/projects/{name}/versions`
2. **Get manifest** — GET `/checkout/{project}/{tag}` returns list of {path, fid, hash, size}
3. **Generate plan** — `SyncEngine.generate_plan()` compares manifest vs local files; detects moves (same hash different path = LOCAL_COPY, zero bandwidth)
4. **Execute plan** — parallel downloads from SeaweedFS filer, local copies, backups per policy
5. **Save state + registry** — same as push
6. **post-pull hooks** — `copalpm project register <path>` + display CopalVX block from pulled project.yaml

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

This is written by `copalpm record copalvx-update` (a CopalPM subcommand) which is called by `pm_hooks.hook_post_push()` after every successful CopalVX push.

**The pm_hooks system is immune to all CopalVX server/API changes** — it only calls external CLIs via subprocess. The hooks are all non-fatal: if `copalpm` is not in PATH, a warning prints and CopalVX continues normally.

**Subprocess calls made by pm_hooks** (post-Phase 2):

| Hook | Command |
|------|---------|
| pre-push  | `copalpm record sync-time --file <yaml>` |
| post-push | `copalpm record copalvx-update --file <yaml> --project-name <n> --version <v>` |
| post-pull | `copalpm project register <abs_path>` |
| post-pull | `copalpm record get copalvx.project_name --file <yaml>` |
| post-pull | `copalpm record get copalvx.last_push --file <yaml>` |

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
    "server_ip": "192.168.1.100",
    "api_port": 8005,
    "filer_port": 8888,
    "default_author": "yourname",
    "default_projects_root": "D:\\Projects",
    "conflict_policy": "backup",
    "client_path": "E:\\Development\\copal\\copalvx\\client"
}
```

| Key | Purpose |
|-----|---------|
| `server_ip` | Server address — all API/filer endpoints derive from this |
| `api_port` | FastAPI port (default 8005) |
| `filer_port` | SeaweedFS filer port (default 8888) |
| `default_author` | Used in commits when no author specified |
| `default_projects_root` | Default root for project folders |
| `conflict_policy` | Default pull conflict resolution: `backup` (rename to .bak), `overwrite`, or `skip`. Shown as default in `do_pull()` prompt; configurable via `copalvx setup`. |
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
PUBLIC_ACCESS_HOST=192.168.1.100
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

## Completed phases (as of 2026-05-10)

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
| Part 3 | Dashboard TUI: rewrote tui.py as a terminal dashboard — health indicators, project list table, project detail (versions + delete), push/pull as secondary backup actions. Added get_health/list_projects/get_metadata/delete_project to api.py | tui.py, api.py |
| QoL | Rename project: PATCH /projects/{name} server endpoint + rename_project() in api.py + confirm_rename() + [N] key in tui.py. Fixed delete 500 (FK ordering bug: must delete project first so CASCADE clears project_files before assets can be removed). do_push() no longer auto-increments version tag when server already has versions — tag prompt is blank, user types it. | main.py, api.py, tui.py |
| QoL-pm | pm-tui project management: CopalVXRenameModal ([N]) renames CopalVX project + updates project.yaml; CopalVXDeleteModal ([X]) deletes from server; DeleteProjectModal ([D]) deletes PM project from registry, optional local folder deletion, optional CopalVX server deletion. _NNN folder suffix is now opt-in checkbox in InitScreen (was mandatory). Scrolling fixed: flattened ScrollableContainer(Vertical) → ScrollableContainer(id="detail-body") + height:1fr. Added rename_project()/delete_project() to copalvx_api.py. | ProjectRegistry tui_app.py, copalvx_api.py |
| QoL-pm-2 | Template system: user-defined templates stored in templates.json (DATA_DIR). Each template defines preset metadata + folder structure. TemplateScreen ([T] from dashboard) with [N]/[E]/[D] for full CRUD. EditTemplateModal for create/edit. InitScreen RadioSet built dynamically from loaded templates; _do_create() reads folder list from template (supports nested paths). Form scrolling fixed for InitScreen and EditTemplateModal (inner ScrollableContainer with max-height: 55vh, buttons outside scroll area). CVX update indicator: dashboard background-polls CopalVX server every 60s for each project's latest version; shows yellow "↑ vX.Y" in name cell when server version differs from local project.yaml last_push_version. | ProjectRegistry config.py, pm.py, tui_app.py |
| Dashboard redesign | Replaced DataTable with scrollable list of ProjectRow widgets (height 3). Each row shows project name, Open Folder button, ▲ push, ▼ pull buttons. Server-only CVX projects shown greyed out below a Rule separator (▼ pull only). Search Input filters by name/ID in real-time; Checkbox toggles server project inclusion. Arrow-key navigation between rows, Enter opens detail. Push/pull work directly from dashboard rows. _cvx_next_tag() and _cvx_stream() extracted to module-level; list_projects() added to copalvx_api.py. | ProjectRegistry tui_app.py, copalvx_api.py |
| QoL-2 | **B:** fs.py + sync.py informational prints TTY-gated (`_verbose = sys.stdout.isatty()`); ⚠️ warnings still unconditional. **A:** `do_push()` reads `.copal/state.json` to pre-fill project name default (falls back to folder basename). **C:** `conflict_policy` added to `DEFAULT_CONFIG` + `setup_cli` + shown as pre-selected default in `do_pull()` prompt. **D:** `projects.description` returned by metadata endpoint; `PATCH /projects/{name}/description` endpoint added; `[E]dit notes` in TUI project detail. **E:** `[F]iles` browser in project detail — pick a version, see full file list with per-file sizes + total. **F:** `total_storage_bytes` (unique blob bytes across all versions) added to `GET /projects`. **G:** `GET /server/stats` endpoint + dashboard stats line (projects / versions / blobs / bytes). **H:** `do_push()` reordered: scan+handshake+preview shown before tag/message prompts. | main.py, api.py, config.py, fs.py, sync.py, tui.py |
| Security audit | Full codebase audit → 15 findings (2 critical, 4 high, 4 medium, 5 low). Fixed: **C1** path traversal in SyncEngine, **C2** broken `IN :tuple` → `= ANY(:list)` (silent multi-file failure), **H1** state saved only on clean pull, **H2** LEFT JOIN in metadata (no 404 on zero-commit project), **H3** `POST /admin/cleanup-orphans` endpoint, **H4** silent SeaweedFS blob-leak on delete replaced with logged warnings, **L2** removed leaking `client_id` from handshake, **L3** 10 MB request body limit middleware, **L6** server-side version tag regex guard, **M1** registry write hardened with mkdir+try/except, **M3** `POST /confirm_uploads` bulk endpoint (N+1 → 1 round-trip), **M4** dedup by hash before upload, **M5** hash-mismatch triggers retry (not immediate failure), **M6** SeaweedFS filer port via env var, **M7** `cls`/`clear` on Windows/Unix. 48 automated tests added (27 unit, 21 integration). | sync.py, transport.py, registry.py, tui.py, main.py, api.py, .env.example, pyproject.toml, tests/ |
| Phase I | Version diff endpoint: `GET /projects/{name}/diff/{v1}/{v2}` (FULL OUTER JOIN, no SeaweedFS reads); `api.get_diff()`; `_show_diff()` TUI helper; `[D]iff` in `[F]iles` browser inner loop. | server/app/main.py, client/copal_core/api.py, client/tui.py |
| Phase J | Selective pull: `_changed_folders()` + `_matches_prefix()` helpers; `do_pull()` shows numbered folder list from diff, filters manifest; `pull_cli()` + `--prefix` flag; `SelectivePullModal` in pm-tui with checkboxes; `get_diff()` + `extract_changed_folders()` + updated `run_pull()` in copalvx_api.py; state save skipped on partial pull. | client/tui.py, ProjectRegistry copalvx_api.py, tui_app.py |
| Phase C | Smart per-file conflict resolution: `generate_plan(last_manifest_hashes=None)` in SyncEngine; untouched files auto-overwrite, edited files auto-backup; `do_pull()` fetches last manifest + shows smart-mode notice; `pull_cli()` applies smart mode silently; falls back to global policy when no state available. | client/copal_core/sync.py, client/tui.py |
| pm-tui UI fixes | (1) `#detail-body { padding-top: 1 }` — content no longer flush against Header. (2) `ProjectRow Button` — removed `height: 1` (clipped borders invisible); `min-width: 3→5`. (3) `InitScreen`/`EditTemplateModal` scrolling — outer box `height: 85vh` (was `auto`), scroll area `height: 1fr` (was `max-height: 55vh`), and removed `Vertical(id="custom-fields")` wrapper (flat direct children with `.custom-field` class for show/hide toggling). (4) CopalVX storage stats in `ProjectDetailScreen` — `get_project_stats()` in `copalvx_api.py` + `_cvx_stats` field + `_fetch_cvx_stats()` background thread + Server ver/Versions/Storage rows in `_build()`. | ProjectRegistry tui_app.py, copalvx_api.py |

---

## Current API surface

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/projects` | Create project (201/409) |
| GET | `/projects` | List all projects with stats (includes `total_storage_bytes` per project) |
| PATCH | `/projects/{name}` | Rename project (body: `{new_name: str}`; 404/409) |
| PATCH | `/projects/{name}/description` | Update project notes (body: `{description: str}`; 404) |
| GET | `/projects/{name}/versions` | List versions (newest first) |
| GET | `/projects/{name}/diff/{v1}/{v2}` | File diff between two versions (added/removed/changed/unchanged_count) |
| GET | `/projects/{name}/metadata` | Project detail (includes `authors`, `description`) |
| DELETE | `/projects/{name}` | Delete project (body: `{delete_orphan_files: bool}`) |
| POST | `/handshake` | Compare client manifest with server |
| POST | `/get_upload_urls` | Request SeaweedFS FIDs for upload |
| POST | `/confirm_upload` | Record uploaded blob (with HEAD verification) |
| POST | `/commit` | Create version (atomic, validates all hashes) |
| GET | `/checkout/{name}/{tag}` | Get file manifest for a version |
| GET | `/health` | Service health (DB + SeaweedFS connectivity) |
| GET | `/server/stats` | Server-wide totals: projects, versions, unique blobs, bytes stored |

---

## Feature history and roadmap

**Design decision (2026-05-06):** CopalVX TUI becomes a service dashboard. Push/pull moves to pm-tui as the primary interface. CopalVX TUI keeps push/pull as a backup (standalone use without pm).

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

### Part 3 — CopalVX dashboard TUI (COMPLETE)

Rewrote `client/tui.py` as a terminal dashboard:
- `show_dashboard()` — health bar (API/DB/SeaweedFS), project list table (name, latest version, version count, last push, author)
- `show_project(name)` — metadata header (size, authors, message), full version list (capped at 15 + overflow)
- `confirm_delete(name)` — two-step delete: orphan blob option + name confirmation
- `do_push(preset_project=None)` / `do_pull(preset_project=None)` — interactive push/pull, accept preset project name from project detail screen
- `push_cli` / `pull_cli` / `main()` / argparse dispatch — **unchanged** (pm-tui subprocess contract preserved)
- ANSI colour (green/red/yellow/cyan) — auto-disabled when stdout is piped (`sys.stdout.isatty()`)
- Added to `api.py`: `get_health()`, `list_projects()`, `get_metadata()`, `delete_project()`

**Navigation:**
- Dashboard: `[1-N]` open project → `[P]` push → `[L]` pull → `[R]` refresh → `[Q]` quit
- Project detail: `[P]` push → `[L]` pull → `[N]` rename → `[E]` edit notes → `[F]` files → `[D]` delete → `[B]` back

### QoL additions (COMPLETE)

**CopalVX TUI (`client/tui.py`):**
- `[N]ame` in project detail — renames a CopalVX project on the server; prompts for new name, calls `PATCH /projects/{name}`, updates display in-place
- `do_push()` version tag prompt no longer auto-increments: when the project has existing versions, the prompt is blank and the user types the tag manually (latest version shown as context). New projects still default to `v1.0`.
- Delete 500 fix: `DELETE /projects/{name}` now deletes the project row first (cascades → commits → project_files), then removes orphan assets. Previously tried to delete assets while project_files still held FK references.

**pm-tui (`E:\Development\ProjectRegistry\src\copalpm\tui_app.py` — renamed from `src/project_registry/` in Phase 2):**

Key bindings in `ProjectDetailScreen`:

| Key | Action |
|-----|--------|
| `P` | Push to CopalVX |
| `L` | Pull from CopalVX |
| `N` | Rename CopalVX project (updates server + local project.yaml) |
| `X` | Delete CopalVX project from server (optional orphan blob cleanup) |
| `D` | Delete PM project: removes from registry, optional local folder delete, optional CopalVX server delete |
| `T` | Start/stop time tracker |
| `R` | Refresh |

`InitScreen` new project form: `_NNN` folder suffix is now an opt-in `Checkbox` (unchecked by default). Previously appended `_001` to every project folder name.

**Scrolling fix:** `ProjectDetailScreen` previously had `ScrollableContainer(Vertical(id="detail-body"))`. Setting `height: 1fr` on the outer container caused the inner `Vertical` to collapse to zero height — all content invisible. Fixed by flattening to `ScrollableContainer(id="detail-body")` and mounting content directly into the scroll container.

### QoL-pm-2 additions (COMPLETE, 2026-05-07)

**Template system (`E:\Development\ProjectRegistry`):**
- Storage: `templates.json` in `DATA_DIR` (`%APPDATA%/project-registry/` on Windows)
- `config.py`: `TEMPLATES_FILE` path constant
- `pm.py`: `DEFAULT_TEMPLATES` list (Tactical + Digital Signage, each with `folders` list), `load_templates()` (seeds defaults on first run), `save_templates()`
- `tui_app.py` — `TemplateScreen`: DataTable with [N] new / [E] edit / [D] delete; `EditTemplateModal`: form with name, type, category, client, director, producer, folders (comma-separated input; supports nested paths like `02_Workfiles/Houdini`); `DashboardScreen [T]` opens templates; `InitScreen` builds RadioSet dynamically from `load_templates()` — adding/editing a template is reflected immediately next time New Project is opened
- Folder creation uses `parents=True` so templates can define nested subdirectories

**Form scrolling fix (`InitScreen` + `EditTemplateModal`):**
- Same root cause as the earlier `ProjectDetailScreen` fix: `ScrollableContainer(Vertical(...))` collapses inner widget
- Pattern for scrollable centered forms: outer styled box (`Vertical(id="box")`) is the direct child of the Screen; a flat `ScrollableContainer(id="scroll")` lives inside it wrapping form fields; buttons stay outside the scroll container; CSS: `#scroll { max-height: 55vh; }` (bounds the scroll area without fixing the outer box height)

**CVX update indicator (`DashboardScreen`):**
- Each local project row includes `cvx_name` + `cvx_local_version` from `project.yaml`
- Background thread fetches server versions + project list together every 60s
- `↑` shown in yellow next to project name when server version differs from local record

### Dashboard redesign (COMPLETE, 2026-05-07)

Replaced `DataTable` with a scrollable list of `ProjectRow` widgets in `DashboardScreen`.

**`ProjectRow(Widget)`** — `src/project_registry/tui_app.py`:
- `can_focus = True`; height 3 (taller than old table rows)
- Buttons: **Open Folder** (local only), **▲** push (local + CVX only), **▼** pull (all CVX projects)
- Posts messages: `Selected`, `OpenFolder`, `PushRequested`, `PullRequested` — handled by `DashboardScreen`
- Button presses stop event propagation; row-click only fires on non-button area

**`DashboardScreen` layout:**
```
Header
Horizontal (search-row):
  Input (search, filters in real-time)
  Checkbox ("Server projects", toggles server-only visibility)
ScrollableContainer (project-list, padding-top: 1):
  ProjectRow ...  (local projects)
  Rule
  ProjectRow ...  (server-only, greyed out)
Footer
```

**Server-only projects:**
- `copalvx_api.list_projects()` (new — calls `GET /projects`) fetched in same background thread as version poll
- Projects on the server whose name doesn't match any local `cvx_name` appear below the separator
- Greyed out (`.server-only` CSS class), only ▼ pull shown
- Pull for server-only projects defaults to `projects_dir` from config (no local path yet)

**Shared module-level utilities (extracted from `ProjectDetailScreen`):**
- `_open_folder(path)` — cross-platform file manager launch (Windows: `explorer`, Mac: `open`, Linux: `xdg-open`)
- `_cvx_next_tag(versions)` — bumps the last dot-segment of the latest version tag
- `_cvx_stream(proc, modal, app, on_success)` — streams subprocess stdout to `CopalVXProgressModal`; used by Dashboard, ProjectDetail, and auto-push

**Navigation:**
- `↑`/`↓` arrow keys move focus between rows
- `Enter` on a local row → opens `ProjectDetailScreen`
- `Tab` moves between buttons within the focused row

**Dashboard key bindings (current):**

| Key | Action |
|-----|--------|
| `N` | New project |
| `T` | Manage templates |
| `R` | Refresh (data + server poll) |
| `Q` | Quit |

**ProjectDetailScreen key bindings (unchanged):**

| Key | Action |
|-----|--------|
| `P` | Push to CopalVX |
| `L` | Pull from CopalVX |
| `N` | Rename CopalVX project |
| `X` | Delete from CopalVX server |
| `D` | Delete PM project |
| `T` | Start/stop time tracker |
| `R` | Refresh |
| `Esc` | Back |

**TemplateScreen key bindings:**

| Key | Action |
|-----|--------|
| `N` | New template |
| `E` | Edit selected |
| `D` | Delete selected |
| `Esc` | Back |

### QoL-2 additions (COMPLETE, 2026-05-10)

**fs.py + sync.py — TTY-gated verbose prints (B):**
- `_verbose = sys.stdout.isatty()` module-level flag in both files
- Informational prints (`🔍 Scanning directory`, `ℹ️ Loaded .copalignore`, `🔍 SyncEngine: Scanning`, `ℹ️ Indexed N local files`) gated on `_verbose` — silent when stdout is piped (CLI subprocess mode)
- Warning prints (`⚠️`) remain unconditional — always surface errors

**config.py — `conflict_policy` default (C):**
- Added `"conflict_policy": "backup"` to `DEFAULT_CONFIG`
- Existing users get it auto-written on next startup via the config migration in `load_config()`
- `do_pull()` reads `SETTINGS.get("conflict_policy")` and shows it pre-selected in the prompt: `Select [1-3] [1=backup]:`
- `setup_cli()` exposes it as a configurable field

**tui.py `do_push()` — pre-fill from local state (A):**
- After `root_dir` is resolved, `fs.load_local_state(root_dir)` is called
- Default project name: `state["project_id"]` → folder basename (in that priority)
- No change when `preset_project` is set (called from project detail screen)

**tui.py `do_push()` — preview before tag/message (H):**
- Scan + handshake now run immediately after project name confirmed, before any version/message prompts
- Preview block shows: `New files: N (X MB to upload) / Unchanged: Y / Total: Z`
- `Proceed with push? (Y/n)` — abort here to avoid entering tag/message for nothing
- Version tag and commit message prompts appear only after user confirms

**server/app/main.py + client/api.py — storage stats (F + G):**
- `GET /projects` now includes `total_storage_bytes` per project (sum of unique blob bytes across all versions — deduplication-aware)
- `GET /server/stats` — new endpoint: `{total_projects, total_versions, total_unique_blobs, total_storage_bytes}`
- Dashboard stats line: `3 project(s) | 12 version(s) | 4.2 GB stored | 847 unique blob(s)` (fails silently if unreachable)
- Project table: Size column added (Project column trimmed 22→18 chars to fit 80-col terminal)

**server/app/main.py + client/api.py + tui.py — description (D):**
- `GET /projects/{name}/metadata` now returns `description` field (empty string for NULL)
- `PATCH /projects/{name}/description` — new endpoint; 404 if project not found
- `api.update_description(project_name, description)` in client
- `show_project()` shows `Notes:` line if description is non-empty
- `[E]dit notes` in project detail: shows current notes, prompts for new text

**tui.py `show_project()` — version file browser (E):**
- `[F]iles` key in project detail menu
- Shows version list, user selects by number or types a tag
- Calls `api.get_manifest(name, tag)` and renders file list: path, size (right-aligned), total at bottom
- No server changes — reuses existing checkout endpoint

**tui.py `show_project()` — refactor:**
- `meta` and `versions` are now fetched and cached at the top of each loop iteration
- Both available in all choice handlers (needed for `[E]` description edit and `[F]` version selection)

---

### Phase I — Version Diff (COMPLETE, 2026-05-10)

**Server:** `GET /projects/{name}/diff/{v1}/{v2}` — full outer join of `project_files` for both commit IDs; classifies files as added/removed/changed/unchanged. No SeaweedFS reads — all data is in the DB.

Returns:
```json
{
  "v1": "v1.0", "v2": "v2.0",
  "added":     [{"path": "...", "size": N}],
  "removed":   [{"path": "...", "size": N}],
  "changed":   [{"path": "...", "old_size": N, "new_size": N}],
  "unchanged_count": N
}
```

**Client:** `api.get_diff(project, v1, v2)` — wraps the endpoint; returns `None` on 404.

**TUI:** `_show_diff(name, base_tag, versions)` helper. Called from the `[F]iles` browser via `[D]iff`. Auto-orders the pair so the diff always reads older → newer (using position in the versions list). Renders `- removed` (red), `+ added` (green), `~ changed` (yellow) with sizes; summary line at bottom.

**Navigation:** `[F]iles` browser now has an inner loop: show file list → `[D]iff / [Enter] back` → diff screen → back to file list.

### Phase J — Selective pull (COMPLETE, 2026-05-10)

No server changes needed. Manifest is already a flat list of relative paths.

**Folder grouping rule:** Each changed file's immediate parent directory becomes one checkbox option. A file directly in `02_Workfiles/` (e.g. `02_Workfiles/project.aep`) adds `02_Workfiles` to the list; a file in `02_Workfiles/Renders/` adds `02_Workfiles/Renders`. Only folders that have at least one changed file are shown.

**Filter semantics (Option A — full subtree):** Selecting a folder prefix pulls everything under it. `02_Workfiles` selected → all files whose path starts with `02_Workfiles/` are included.

**Client — `tui.py`:**
- `_changed_folders(diff)` — groups diff entries by immediate parent; returns `[{folder, count}]` sorted alphabetically
- `_matches_prefix(path, prefix_set)` — returns True if path's immediate parent or any ancestor matches a prefix in the set (full subtree, not just exact parent)
- `do_pull()` — after manifest fetch, reads local state to find current version, calls `api.get_diff()`, shows numbered folder list with counts; user picks by number or blank=all; filters `files` before passing to `SyncEngine`; state save skipped on partial pull
- `pull_cli()` — accepts `prefixes: list[str] | None`; filters manifest by prefix set; skips state save when selective
- `pull` argparse subparser — `--prefix` flag (repeatable `action="append"`); passed to `pull_cli`

**Client — `copalvx_api.py` (ProjectRegistry):**
- `get_diff(project_name, v1, v2)` — calls `GET /projects/{name}/diff/{v1}/{v2}`; returns `None` on any error
- `extract_changed_folders(diff_result)` — same grouping algorithm as `_changed_folders` in tui.py
- `run_pull()` — updated signature: `prefixes=None`; appends `--prefix <folder>` for each prefix

**pm-tui — `tui_app.py`:**
- `SelectivePullModal(ModalScreen)` — shows version tag, folder checkboxes (one per changed folder with count), **Pull Selected** / **Pull Full Version** / **Cancel** buttons
- `DashboardScreen.on_project_row_pull_requested()` — after version pick, spawns daemon thread to call `get_diff()`; on diff success pushes `SelectivePullModal`; on error or no diff falls through to full pull
- `ProjectDetailScreen.action_pull_copalvx()` — same pattern; uses `last_push_version` from project.yaml as the "from" version for diff

**Tests:**
- `client/tests/unit/test_tui_helpers.py` — `TestChangedFolders` (12 tests) + `TestMatchesPrefix` (12 tests); no server required
- `client/tests/integration/test_server.py` — `TestVersionDiff` (5 tests): 404 for missing project/version, response structure, tag echo, same-version zero-change

**Dependency order:** C (smart per-file conflict resolution) depends on J and uses diff data + state.json.

---

### Phase C — Smart per-file conflict resolution (COMPLETE, 2026-05-12)

No server changes needed. All logic is in `SyncEngine` and `do_pull()`/`pull_cli()`.

**How it works:** When pulling, `do_pull()` and `pull_cli()` try to load the manifest for the locally recorded `last_tag` (from `.copal/state.json`). This gives a `{path: hash}` map of every file as it was when last synced. `SyncEngine.generate_plan()` receives this map as `last_manifest_hashes`.

For each file that conflicts (exists locally but differs from the target version):
- `local_hash == last_manifest_hash` → file is untouched since last sync → **auto-overwrite** (no backup needed, no user decision required)
- `local_hash != last_manifest_hash` OR path absent from last manifest → file was modified locally → **auto-backup** (`.bak` created automatically)

Falls back gracefully to the global `conflict_policy` if:
- No `.copal/state.json` exists (first pull)
- Manifest fetch for `local_tag` fails (network error, version deleted)
- `local_tag == tag` (re-pulling the same version, no "last" to compare against in `pull_cli`)

**`sync.py`:** `generate_plan(server_manifest_files, local_root, last_manifest_hashes=None)` — new optional parameter. When provided, replaces the global-policy branch with the per-file hash comparison.

**`tui.py` `do_pull()`:**
- Manifest fetch moved before the conflict section (needed for smart-mode decision)
- After selective-pull section: fetches last manifest and builds `last_manifest_hashes`
- Smart mode: prints `Smart conflict mode (comparing against vX.Y)` instead of showing policy prompt
- Plan summary now shows `Auto-update: N (unchanged locally)` + `Backed up: N (your edits → .bak)` in smart mode
- Falls back to the existing global-policy prompt when smart data is unavailable

**`tui.py` `pull_cli()`:** reads local state from `target` dir; if `local_tag` found and project matches, fetches last manifest silently; passes `last_manifest_hashes` to `generate_plan()`; prints one line `Smart conflict detection active (comparing against vX.Y)`.

**Tests:** `TestSmartConflict` (10 tests) in `client/tests/unit/test_sync.py` — untouched→overwrite, edited→backup, absent-from-last→backup, perfect-match→skip, missing-file→download, global-policy fallback (backup/overwrite/skip), backslash normalisation, mixed multi-file scenario.

---

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
cd E:\Development\copal\copalvx\client
uv run copalvx         # recommended (requires uv sync first)
uv run python tui.py   # also works
```

---

## Testing

### Running the suite

```powershell
cd E:\Development\copal\copalvx\client

# All tests (unit + integration)
uv run pytest -v

# Unit tests only (no server needed)
uv run pytest tests/unit/ -v

# Integration tests only (server must be up)
uv run pytest tests/integration/ -v
```

**48 tests total — 27 unit, 21 integration.**
Integration tests auto-skip if the server is unreachable (`_ensure_server` autouse fixture tries `GET /health` with a 4 s timeout and calls `pytest.skip` on failure).

### Test files

| File | What it tests | Fixture / requirement |
|------|---------------|----------------------|
| `tests/unit/test_sync.py` | **C1** — `SyncEngine.generate_plan()` path-traversal guard: `../` traversal, absolute paths, Windows paths, mixed manifests, safe `a/../b` normalization | `tmp_path` only — no network |
| `tests/unit/test_transport.py` | **M5** — `transport.download_file()` hash-mismatch retry: success, mismatch triggers retry, exhausted retries, backoff schedule, 404 not retried | Mocks `transport.session.get` + `_hash_file` + `time.sleep` |
| `tests/unit/test_versioning.py` | `ensure_prefix`, `increment_tag` (parametrized), `validate_push_tag` — all tag parsing edge cases | Pure unit — no I/O |
| `tests/integration/test_server.py` | Live API: **H2** (zero-commit metadata), **C2** (multi-file handshake), **M3** (bulk confirm), **L6** (tag validation), **H3** (cleanup-orphans), **L3** (body size limit), `/health` endpoint | Requires `docker-compose up -d` |

### Integration test design

- A module-scoped `project` fixture creates a UUID-named project (`__pytest_<hex>__`) before tests run and deletes it (including orphan blobs) after — leaves the server clean.
- `COPALVX_SERVER_URL` env var overrides the default `http://192.168.1.100:8005`.
- `TestBodySizeLimit` sends a genuine 11 MB payload (`b"x" * 11_534_336`) — `requests` always sets real `Content-Length` so the fake-header approach doesn't work. Catches `ConnectionError` as also-valid rejection (server may close the socket after sending 413 mid-upload).

### Dev dependencies

Declared in `client/pyproject.toml`:
```toml
[tool.uv]
dev-dependencies = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts   = "-q"
```

`uv sync` installs them automatically. `uv run pytest` picks up the config without any extra flags.

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

5. **projects.description** is stored in the DB and editable via `PATCH /projects/{name}/description`. `GET /projects/{name}/metadata` returns it as `""` when NULL. Older projects have NULL — `get_project_metadata` normalises this, no action needed.

6. **The client registry (`~/.copal/projects.json`) is separate from ProjectRegistry.** It's just a "recently used" list for the TUI's convenience. Max 20 entries.

7. **`client_path` must be set explicitly in `~/.copal/config.json`.** Auto-detection via `Path(__file__)` is unreliable because `__file__` resolves to the installed site-packages location (e.g. `.venv/Lib/site-packages/copal_core/config.py`), not the source directory. pm-tui reads the raw JSON — it doesn't use client-side config merging.

8. **PowerShell `Set-Content -Encoding utf8` writes UTF-8 with BOM on Windows PowerShell 5.1.** Python's `json.loads()` chokes on the BOM with "Unexpected UTF-8 BOM" error. Fix: use `encoding="utf-8-sig"` when reading, or write with `[System.Text.UTF8Encoding]::new($false)` from PowerShell. `copalvx_api.py` already uses `utf-8-sig`.

9. **Subprocess stdout encoding defaults to cp1252 on Windows when piped.** Emoji in print statements (`pm_hooks.py` uses them) causes `UnicodeEncodeError: 'charmap' codec can't encode character`. Fix: pass `PYTHONIOENCODING=utf-8` in the subprocess environment.

10. **`PYTHONUNBUFFERED=1` required for real-time subprocess streaming.** Without it, Python buffers stdout in a subprocess, so the parent can't read lines as they're printed. Set it in the subprocess env alongside `PYTHONIOENCODING`. Used by both `pm_hooks` (CopalVX → CopalPM) and `copalvx_api` (CopalPM → CopalVX) directions.

11. **`DELETE /projects/{name}` must delete the project row before orphan assets.** `project_files.asset_id` is a FK to `assets`. Trying to `DELETE FROM assets` while `project_files` still references them causes a FK violation (500). Delete the project first — the `ON DELETE CASCADE` chain clears commits → project_files — then delete orphan assets safely.

---

For TUI/Textual-specific gotchas (`call_from_thread`, `ScrollableContainer`/`Vertical` layout pitfalls, daemon spec, Phase 2 rename) see [../copalpm/CLAUDE.md](../copalpm/CLAUDE.md) — those concerns originated in the CopalPM package and are documented there now.
