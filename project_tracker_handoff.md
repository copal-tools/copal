# Project Tracker — Developer Handoff

**Version:** 0.1.0  
**Platform:** Windows 10 / 11  
**Language:** Python 3.11  
**Package manager:** uv  
**Service runner:** NSSM  
**Storage:** Flat files — no database  

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture & File Layout](#2-architecture--file-layout)
3. [Project ID & Naming Convention](#3-project-id--naming-convention)
4. [Data Schemas](#4-data-schemas)
5. [CLI Reference — pm](#5-cli-reference--pm)
6. [HTTP Service — task_tracker](#6-http-service--task_tracker)
7. [Setup Guide (From Zero)](#7-setup-guide-from-zero)
8. [Daily Use Workflow](#8-daily-use-workflow)
9. [Known Gaps & Planned Features](#9-known-gaps--planned-features)
10. [pyproject.toml Reference](#10-pyprojecttoml-reference)
11. [Quick Reference Card](#11-quick-reference-card)
12. [CopalVX Integration](#12-copalvx-integration-added-2026-05-06)

---

## 1. Overview

Project Tracker is a lightweight, file-based project and time-tracking system for Windows power users and creative professionals (e.g. After Effects artists, editors). It has no database, no web UI, and no cloud dependencies — everything lives as plain JSON and JSONL files under `%ProgramData%\ProjectTracker`.

**Design philosophy:** keep it as simple as possible — one CLI tool, one background service, plain text files. No migrations, no ORM, no web framework overhead beyond a tiny Flask + Waitress HTTP service.

### Core Components

**`pm.py` — Project Manager CLI** (stdlib only, no dependencies)
- Creates structured project folders and registers them in a central JSON registry
- Generates deterministic project IDs based on title slug + date + optional increment
- Reads the session log to roll up time per project

**`task_tracker.py` — Time-Tracking HTTP Service** (Flask + Waitress)
- Runs on `localhost:5123`, authenticated with a random API key
- Tracks one active session at a time; auto-stops on inactivity
- External tools (e.g. AE scripts, PowerShell) call the REST endpoints

---

## 2. Architecture & File Layout

### Repository Structure

```
project-tracker/
  pyproject.toml                        # Project metadata, deps, entry-points (PEP 621)
  uv.lock                               # Locked dependency graph (commit this)
  src/
    project_tracker/
      __init__.py                       # Empty — marks it as a package
      pm.py                             # CLI — project management
      task_tracker.py                   # HTTP service — session tracking
```

### Runtime Data Files

All runtime state lives under `%ProgramData%\ProjectTracker\` — created automatically on first run.

```
%ProgramData%\ProjectTracker\
  config.json               # Service config: api_key, port, idle_minutes
  projects.json             # Registry array — all known projects
  current_session.json      # Open session (deleted on stop)
  sessions.jsonl            # Append-only log — one closed session per line
  service.out.log           # NSSM stdout log
  service.err.log           # NSSM stderr log
```

### Per-Project Folder Structure

Each project created with `pm init` produces the following layout:

```
<project_root>/
  meta.json         ← project metadata snapshot
  01_Intake/        ← raw incoming files
  02_Workfiles/     ← working files (AE project, edits, etc.)
  03_Exports/       ← finished deliverables
```

---

## 3. Project ID & Naming Convention

Project IDs are generated deterministically by `pm init` and are never random.

### Format

```
PROJ-{SLUG}-{DDMMYY}           (basic)
PROJ-{SLUG}-{DDMMYY}_{NNN}     (with --inc flag)
```

| Part | Description |
|------|-------------|
| `SLUG` | Title uppercased, spaces → hyphens, non-alphanumeric stripped |
| `DDMMYY` | Date of creation, e.g. `280426` for April 28 2026 |
| `NNN` | 3-digit zero-padded suffix, e.g. `001` (only with `--inc`) |

### Examples

```
pm init "Acme Ad Cutdown"
→ PROJ-ACME-AD-CUTDOWN-280426

pm init "Acme Ad Cutdown" --inc
→ PROJ-ACME-AD-CUTDOWN-280426_001
→ PROJ-ACME-AD-CUTDOWN-280426_002   (next call, same day)
```

> **Note on `--inc`:** The suffix is determined by scanning the last 3 digits of ALL existing subdirectory names in the target base directory — not just ProjectTracker-managed ones. The highest found number + 1 is used.

---

## 4. Data Schemas

### projects.json

Array of objects. Human-editable. Updated by `pm init` and `pm remove`.

```json
[
  {
    "id":         "PROJ-ACME-AD-CUTDOWN-280426",
    "name":       "Acme Ad Cutdown",
    "path":       "D:\\Work\\Acme\\AdCutdown",
    "created_at": "2026-04-28T10:21:33Z"
  }
]
```

### meta.json (per-project root)

```json
{
  "id":         "PROJ-ACME-AD-CUTDOWN-280426",
  "name":       "Acme Ad Cutdown",
  "created_at": "2026-04-28T10:21:33Z",
  "notes":      ""
}
```

### config.json

Auto-generated on first service start. Edit to change port or idle timeout.

```json
{
  "api_key":      "a3f...long hex string...",
  "port":         5123,
  "idle_minutes": 20
}
```

### current_session.json (open session)

```json
{
  "session_id": "S-20260428-ab12cd",
  "project_id": "PROJ-ACME-AD-CUTDOWN-280426",
  "task":       null,
  "start":      "2026-04-28T09:10:01Z",
  "last_ping":  "2026-04-28T09:22:10Z"
}
```

### sessions.jsonl (one line per closed session)

```json
{"session_id":"S-20260428-ab12cd","project_id":"PROJ-ACME-AD-CUTDOWN-280426","task":null,"start":"2026-04-28T09:10:01Z","end":"2026-04-28T11:05:44Z","duration_sec":6943,"stop_reason":"manual"}
```

**`stop_reason` values:** `manual` · `switch` · `inactivity` · `crash_recovery` (future)

---

## 5. CLI Reference — pm

Invoked as `uv run pm <command>` from the project root, or `pm <command>` if the venv is activated.

### `pm init`

```
pm init "<Project Title>" [--dir <base_path>] [--inc]
```

| Argument | Description |
|----------|-------------|
| `name` (positional) | Project title — used to generate the ID and folder name |
| `--dir <path>` | Base directory to create the project folder in. Defaults to CWD |
| `--inc` | Append auto-incremented `_NNN` suffix (scans existing sibling dirs) |

- **Output:** Prints the new project ID to stdout, e.g. `PROJ-MY-PROJECT-280426`
- **Side effects:** Creates folder structure, writes `meta.json`, appends to `projects.json`
- **Error:** Exits with code 1 if the target folder already exists and `--inc` was not passed

### `pm list`

```
pm list
```

Prints all registered projects: `PROJ-... - Project Name - D:\path\to\folder`

### `pm rollup`

```
pm rollup
```

Reads `sessions.jsonl` and prints total hours per project, sorted by project ID.

> **Note:** No date range filtering in v0.1.0 — planned for a future version.

### `pm remove`

```
pm remove <project_id>
```

Removes the project from the registry (`projects.json`). **Does not delete files on disk.**

- **Exit codes:** `0` on success, `1` if project ID not found

---

## 6. HTTP Service — task_tracker

A Flask application served by Waitress, listening exclusively on `127.0.0.1:5123`. All state-changing endpoints require the header `X-API-Key: <value from config.json>`.

### Endpoints

| Method | Endpoint | Body / Params | Description |
|--------|----------|---------------|-------------|
| GET | `/health` | — | Unauthenticated liveness check |
| GET | `/state` | — | Returns `current_session.json` or null |
| GET | `/projects` | — | Returns sorted list of registered project IDs |
| POST | `/start` | `{projectId, task?}` | Ends any open session (reason: `switch`), starts new one |
| POST | `/stop` | `{reason?}` | Closes open session. Default reason: `manual` |
| POST | `/ping` | — | Resets idle timer, updates `last_ping`. Keeps session alive |

### Authentication

Every endpoint except `/health` checks the `X-API-Key` header against the value in `config.json`. The key is a 48-character hex string generated with `os.urandom(24).hex()` on first startup.

```powershell
# Get your API key
$cfg  = Get-Content "$env:ProgramData\ProjectTracker\config.json" | ConvertFrom-Json
$KEY  = $cfg.api_key
$PORT = $cfg.port
```

### Idle Auto-Stop

A `threading.Timer` fires after `idle_minutes` (default 20) of inactivity. It resets on every `/start` and `/ping` call. When it fires, `stop_current("inactivity")` is called internally.

- The timer is global and restarts atomically under a `threading.Lock`
- `idle_minutes` can be changed in `config.json` (service restart required)
- External tools should call `/ping` every 5–10 minutes to prevent auto-stop

### Project Validation

The `/start` endpoint validates `projectId` against the registry before starting a session.

| Response | Meaning |
|----------|---------|
| `404 unknown_project_id` | The ID is not in `projects.json` — run `pm init` first |
| `409 registry_missing` | `projects.json` doesn't exist yet — run `pm init` to create it |

---

## 7. Setup Guide (From Zero)

### Prerequisites

- Windows 10 or 11
- [uv](https://docs.astral.sh/uv/) installed
- [NSSM](https://nssm.cc) installed and on PATH
- Python 3.11+ (managed by uv — no manual install needed)

### Step 1 — Clone and enter the repo

```bash
cd C:\Dev
git clone <repo_url> project-tracker
cd project-tracker
```

### Step 2 — Create the virtual environment

```bash
uv python install 3.11
uv venv --python 3.11
uv sync
```

This creates `.venv/`, installs Flask + Waitress, and writes `uv.lock`. Commit `uv.lock`.

### Step 3 — Quick smoke test (foreground)

```powershell
# Terminal 1 — start the service
uv run task-tracker

# Terminal 2 — create a project and start a session
$cfg  = Get-Content "$env:ProgramData\ProjectTracker\config.json" | ConvertFrom-Json
$KEY  = $cfg.api_key; $PORT = $cfg.port

uv run pm init "Demo Project" --dir "D:\Work\Demo"
# prints e.g.  PROJ-DEMO-PROJECT-280426

$body = @{projectId="PROJ-DEMO-PROJECT-280426"} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$PORT/start" `
  -Headers @{"X-API-Key"=$KEY} -Body $body -ContentType "application/json"

Start-Sleep 3
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$PORT/stop" `
  -Headers @{"X-API-Key"=$KEY} -Body '{}' -ContentType "application/json"

uv run pm rollup
# PROJ-DEMO-PROJECT-280426: 0.00h
```

### Step 4 — Install as a Windows service (NSSM)

```powershell
$n  = "TaskTracker"
$py = (Resolve-Path .\.venv\Scripts\python.exe)

nssm install $n $py "-m project_tracker.task_tracker"
nssm set $n AppDirectory (Get-Location).Path
nssm set $n Start SERVICE_AUTO_START
nssm set $n AppStdout "$env:ProgramData\ProjectTracker\service.out.log"
nssm set $n AppStderr "$env:ProgramData\ProjectTracker\service.err.log"
nssm start $n
```

To stop or remove the service:

```powershell
nssm stop TaskTracker
nssm remove TaskTracker confirm
```

---

## 8. Daily Use Workflow

```powershell
# Create a new project
uv run pm init "Client Name - Project" --dir "D:\Work\ClientName"
# → PROJ-CLIENT-NAME-PROJECT-280426

# List all projects
uv run pm list

# Start a session (from AE script, PowerShell, etc.)
$body = @{projectId="PROJ-CLIENT-NAME-PROJECT-280426"; task="colour grade"} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$PORT/start" `
  -Headers @{"X-API-Key"=$KEY} -Body $body -ContentType "application/json"

# Keep alive every 5–10 min
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$PORT/ping" `
  -Headers @{"X-API-Key"=$KEY}

# End session
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$PORT/stop" `
  -Headers @{"X-API-Key"=$KEY} -Body '{"reason":"manual"}' -ContentType "application/json"

# View time totals
uv run pm rollup
# PROJ-CLIENT-NAME-PROJECT-280426: 4.72h
# PROJ-ANOTHER-PROJECT-150326:     11.30h
```

---

## 9. Known Gaps & Planned Features

These features were discussed but are **not yet implemented in v0.1.0**:

- **`pm info <project_id>`** — show full metadata for a single project (was in original spec, not built)
- **`pm rollup --from / --to`** — date-range filtering on session rollups
- **Crash recovery on startup** — auto-close stale `current_session.json` left by a service crash
- **Inline code annotations** — was discussed as a next step at end of last session
- **AE / external tool scripts** — `.jsx` or PowerShell helpers to call `/start`, `/ping`, `/stop`

---

## 10. pyproject.toml Reference

```toml
[project]
name            = "project-tracker"
version         = "0.1.0"
requires-python = ">=3.11"
dependencies    = [
  "flask>=3.0",
  "waitress>=3.0",
]

[project.optional-dependencies]
dev = ["ruff>=0.5.0", "pytest>=8.0"]

[project.scripts]
pm           = "project_tracker.pm:main"
task-tracker = "project_tracker.task_tracker:main"

[build-system]
requires      = ["hatchling"]
build-backend = "hatchling.build"
```

---

## 11. Quick Reference Card

### Key Paths

| Name | Path |
|------|------|
| Registry | `%ProgramData%\ProjectTracker\projects.json` |
| Active session | `%ProgramData%\ProjectTracker\current_session.json` |
| Session log | `%ProgramData%\ProjectTracker\sessions.jsonl` |
| Service config | `%ProgramData%\ProjectTracker\config.json` |
| Service logs | `%ProgramData%\ProjectTracker\service.{out,err}.log` |
| Service URL | `http://127.0.0.1:5123` |

### Commands at a Glance

| Command | Description |
|---------|-------------|
| `uv sync` | Install / update dependencies |
| `uv run task-tracker` | Start the HTTP service in foreground |
| `uv run pm init "Name" --dir <path>` | Create a new project |
| `uv run pm list` | List all registered projects |
| `uv run pm rollup` | Show total hours per project |
| `uv run pm remove <id>` | Remove project from registry |
| `nssm start TaskTracker` | Start the background service |
| `nssm stop TaskTracker` | Stop the background service |

### HTTP Endpoints at a Glance

| Endpoint | Description |
|----------|-------------|
| `GET  /health` | Unauthenticated — is the service alive? |
| `GET  /state` | Current session or null |
| `GET  /projects` | List of registered project IDs |
| `POST /start` | Begin tracking `{projectId, task?}` |
| `POST /stop` | End tracking `{reason?}` |
| `POST /ping` | Keep-alive — resets idle timer |

---

## 12. CopalVX Integration (added 2026-05-06)

ProjectRegistry's TUI (`pm-tui`) is the **primary interface** for pushing/pulling CopalVX versions. The integration is subprocess-based — no shared imports between the two repos.

### New file: `src/project_registry/copalvx_api.py`

| Function | Purpose |
|----------|---------|
| `_config()` | Reads `~/.copal/config.json` (handles BOM via `utf-8-sig`) |
| `_base_url()` | Derives API URL from config |
| `_client_path()` | Returns `client_path` from config (required) |
| `get_versions(name)` | GET `/projects/{name}/versions`, returns `[]` on error |
| `health()` | GET `/health`, returns `{"healthy": False}` on error |
| `run_push(...)` | Spawns `uv run copalvx push` subprocess |
| `run_pull(...)` | Spawns `uv run copalvx pull` subprocess |

### TUI additions in `tui_app.py`

| Class/Method | Purpose |
|--------------|---------|
| `CopalVXPushModal` | Modal: input version tag + message |
| `CopalVXPullModal` | Modal: select version from dropdown |
| `CopalVXProgressModal` | Modal: ProgressBar + RichLog, streams subprocess output |
| `_cvx_stream_subprocess()` | Thread helper: reads stdout line-by-line, parses `[UPLOAD]`/`[DOWNLOAD]` progress lines |
| `action_push_copalvx()` | Keybinding `p` — fetches versions, suggests next tag, runs push |
| `action_pull_copalvx()` | Keybinding `l` — fetches versions, user selects, runs pull |
| `_do_auto_push()` | Triggered on mount when `auto_push=True` — pushes v1.0 with progress modal |

### Post-init auto-push

When `InitScreen._do_create()` creates a new project, it passes `auto_push=True` to `ProjectDetailScreen`. On mount (after a 0.3s delay for the screen to render), `_do_auto_push()` fires, pushing "v1.0" with message "Initial version" to CopalVX. The author is left empty so `push_cli` uses `default_author` from `~/.copal/config.json`.

### Prerequisites

`~/.copal/config.json` must contain:
```json
{
    "client_path": "E:\\Development\\Copal-VX\\client"
}
```
Without this key, push/pull raises a clear error explaining what to add.

### Subprocess protocol

pm-tui spawns `uv run copalvx push <project> <tag> <path>` with:
- `cwd` = `client_path` from config
- `PYTHONIOENCODING=utf-8` (prevents cp1252 emoji crash)
- `PYTHONUNBUFFERED=1` (enables real-time line streaming)
- `bufsize=1` + `encoding="utf-8"` on the Popen

Progress lines from the CopalVX CLI follow the format:
```
[UPLOAD] 3/10 filename.exr
[DOWNLOAD] 7/20 texture.png
```
These are parsed by `_cvx_stream_subprocess()` to update the ProgressBar.

### Known gotchas (pm-tui ↔ CopalVX)

1. **`client_path` is mandatory** — auto-detection via `Path(__file__)` was removed because it resolves to site-packages, not the source dir. Must be set explicitly.

2. **UTF-8 BOM in config.json** — PowerShell 5.1's `Set-Content -Encoding utf8` writes BOM. Use `utf-8-sig` when reading JSON, or write with `[System.Text.UTF8Encoding]::new($false)`.

3. **cp1252 encoding crash** — Windows subprocess stdout defaults to cp1252 when piped. Emoji characters in CopalVX print statements crash without `PYTHONIOENCODING=utf-8` in env.

4. **`call_from_thread` lives on App** — In Textual, background threads must use `self.app.call_from_thread()`, not `self.call_from_thread()`. The method doesn't exist on Screen.

5. **`PYTHONUNBUFFERED=1` required** — Without it, subprocess output is buffered and the progress modal sees nothing until the process exits.

---

*Project Tracker v0.1.0 — Internal Documentation*
