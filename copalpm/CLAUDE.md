# CLAUDE.md — CopalPM

> AI-assistant orientation for the CopalPM package.
> For monorepo-wide context see [../CLAUDE.md](../CLAUDE.md).
> For user-facing install/usage see [README.md](./README.md).
> Last updated: 2026-05-13.

---

## What CopalPM is

Terminal project management + time tracking for motion design and VFX work. File-based — no database, no cloud. Each project is a folder containing `project.yaml`; the user's machine has a registry pointing at registered folders + a sessions log.

The single `copalpm` binary fronts everything via subcommand groups. With no args, it launches the TUI (the most common entry point).

Renamed from `ProjectRegistry` (and the package was `project_registry/`) in Phase 2 of the rebrand. The user data directory **still uses the legacy name** `project-registry/` — see gotcha #1.

---

## Layout

```
copalpm/
├── pyproject.toml
├── src/copalpm/
│   ├── __init__.py
│   ├── cli.py             # Unified argparse dispatcher (single entry point)
│   ├── pm.py              # Project registry ops (init/list/status/register/scan/remove/rollup, service install)
│   ├── project_record.py  # project.yaml ops (show/get/set/phase/validate/sync-time/copalvx-update)
│   ├── time_cli.py        # Time tracking client (start/stop/status/log)
│   ├── task_tracker.py    # Background HTTP daemon (Flask + Waitress, port 5123)
│   ├── deliver_cli.py     # `deliver` subcommand handler
│   ├── tui_app.py         # Textual TUI (entry point for `copalpm` with no args)
│   ├── copalvx_api.py     # Outbound subprocess wrapper around `copalvx` CLI
│   └── config.py          # Shared paths/constants
└── tests/
    ├── unit/
    │   ├── test_imports.py     # All modules + handlers import cleanly
    │   └── test_cli_parser.py  # Every documented argparse invocation
    └── integration/
        └── test_subcommands.py # Spawns `copalpm` binary, exercises read-only ops
```

---

## CLI surface

```
copalpm                            # default → launch TUI

copalpm project init <name>        # create + register a new project
copalpm project list               # list registered projects
copalpm project status [--json]    # summary table of all projects
copalpm project register <path>    # register an existing folder
copalpm project scan <dir>         # find + register projects in a tree
copalpm project remove <id>        # remove from registry (keeps files)
copalpm project rollup [--json]    # total time per project

copalpm record show                # pretty-print this project's record
copalpm record get <field>         # read field from project.yaml
copalpm record set <field> <val>   # write field to project.yaml
copalpm record phase <phase>       # log a phase transition
copalpm record validate            # schema check
copalpm record sync-time           # flush sessions.jsonl into time_entries
copalpm record copalvx-update      # write CopalVX metadata (called by VX post-push hook)

copalpm time start [desc]          # start tracking session
copalpm time stop                  # stop current session
copalpm time status                # show current session
copalpm time log <min> <desc>      # manually log time

copalpm service install            # install task-tracker background service
copalpm service uninstall          # remove the service
copalpm service status             # service state

copalpm deliver <path> [...]       # log a delivered asset
copalpm task-tracker               # daemon entry point (hidden — invoked by the OS service)
```

`record` operates on the `project.yaml` in the CWD (walks up) or via `--file <path>` / `--project <id>`.

---

## Storage

Per-user data lives at:
- macOS / Linux: `~/.config/project-registry/`
- Windows: `%APPDATA%\project-registry\`

Files:
- `registry.json` — list of registered projects (path + metadata)
- `sessions.jsonl` — append-only session log (one JSON object per line)
- `templates.json` — user-defined project templates (seeded with defaults on first run)
- `config.json` — task-tracker service config (port, API key)

---

## Background service

The `copalpm task-tracker` subcommand runs as a daemon, installed via `copalpm service install`. It serves a Flask HTTP API on `127.0.0.1:5123` with all state-changing endpoints behind an `X-API-Key` header. `copalpm time start/stop/status` is a thin client that talks to it.

Service install internals:
- **macOS:** writes a launchd plist at `~/Library/LaunchAgents/com.copal-tools.copalpm.task-tracker.plist`. `ProgramArguments` is a 2-element array: `[<copalpm binary>, "task-tracker"]`. Loaded via `launchctl bootstrap gui/<uid>`.
- **Windows:** uses NSSM (`winget install NSSM.NSSM`). Service named `CopalPMTaskTracker`. NSSM install args: `[<copalpm.exe>, "task-tracker"]`. Pins `APPDATA` env so the service writes to the installing user's data dir.

---

## Integration with CopalVX

CopalPM doesn't depend on CopalVX. CopalVX optionally calls into CopalPM via subprocess (5 sites in CopalVX's `pm_hooks.py`):

| CopalVX event | CopalPM subcommand invoked |
|---------------|----------------------------|
| pre-push      | `copalpm record sync-time --file <yaml>` |
| post-push     | `copalpm record copalvx-update --file <yaml> --project-name <n> --version <v>` |
| post-pull     | `copalpm project register <abs_path>` |
| post-pull     | `copalpm record get copalvx.project_name --file <yaml>` |
| post-pull     | `copalpm record get copalvx.last_push --file <yaml>` |

The `tests/integration/test_pm_hooks_contract.py` in CopalVX verifies these five subcommand paths exist with the expected flag shapes, so a future rename in CopalPM is caught at test time.

CopalPM's outbound calls into CopalVX (when pushing/pulling from the TUI) go through `copalvx_api.py`, which subprocesses `copalvx push|pull` and streams progress.

---

## Tests

```powershell
uv sync --directory copalpm                       # one-time
uv run --directory copalpm pytest                 # run all tests (~19s)
uv run --directory copalpm pytest tests/unit/     # unit only (~1s)
```

61 tests:
- 12 import tests (every module + handler resolves; no `from project_registry` references remain)
- 42 argparse tests (every documented subcommand invocation, required args, mutually-exclusive groups, hidden `task-tracker`)
- 7 integration tests (live binary spawn, read-only operations)

Integration tests auto-skip if the `copalpm` binary is not in the venv.

---

## Gotchas

1. **The user data directory is named `project-registry/`, not `copalpm/`.** Intentional — preserves existing data across the Phase 2 rebrand without forcing migration. Defined in `src/copalpm/config.py`. A migration to `copalpm/` is a tracked follow-up in MERGE_PLAN.md.

2. **`task-tracker` is hidden from `copalpm --help` but still callable.** The OS service spec invokes `copalpm task-tracker`. `cli._build_parser()` filters it out of `_choices_actions` after construction (argparse's `help=SUPPRESS` leaves an ugly `==SUPPRESS==` literal in `--help` output; the filter cleans that up).

3. **Pre-rebrand service installs must be uninstalled before installing the new one.** Old plist label was `com.projectregistry.task-tracker` / NSSM service was `TaskTracker`. The new commands won't touch the old service entries. Anyone migrating must run `pm uninstall-service` from the old install before `copalpm service install` from the new one — otherwise the old service keeps trying to invoke a `task-tracker` binary that no longer exists.

4. **Daemon spec changed in Phase 2.** Before: standalone `task-tracker(.exe)` binary registered directly with the OS service manager. After: the `copalpm` binary is registered, with `task-tracker` as the first argument. macOS plist `ProgramArguments` is a 2-element array; NSSM install passes `task-tracker` as the service args. Reflected in `pm.py`'s `cmd_install_service`.

5. **`call_from_thread()` is on `App`, not `Screen`.** In Textual, use `self.app.call_from_thread()` not `self.call_from_thread()` when calling from a background thread inside a Screen subclass. Multiple places in `tui_app.py` rely on this pattern (e.g. `DashboardScreen`'s 60s server-version poll).

6. **Textual scrollable centered forms have a fragile layout pattern.** `ScrollableContainer(Vertical(...))` with `height: 1fr` collapses the inner `Vertical` to zero. Correct pattern: `Vertical(id="box")` as the direct child of the Screen with **`height: 85vh`** (fixed — `height: auto` breaks `vh`/`1fr` resolution in children); flat `ScrollableContainer(id="scroll")` inside with **`height: 1fr`**; all form fields as flat direct children of the `ScrollableContainer`; buttons outside, below it. See `InitScreen` and `EditTemplateModal` for the canonical implementation. **Never nest a `Vertical` inside a `ScrollableContainer` to group toggle-able fields** — it clips virtual height; use a CSS class instead and `query(".class")` for bulk display toggling.

7. **Subprocess stdout encoding defaults to cp1252 on Windows when piped.** Emoji in print statements causes `UnicodeEncodeError: 'charmap' codec can't encode character`. Fix in any code that spawns Python subprocesses on Windows: pass `PYTHONIOENCODING=utf-8` in the subprocess environment. Also pass `PYTHONUNBUFFERED=1` if you need real-time line streaming from the child process.

8. **Background threads in `DashboardScreen` must use `self.app.call_from_thread()`.** DashboardScreen is the root screen and never gets popped, so holding `self` in a daemon thread is safe. Other screens that can be popped should avoid long-lived threads or guard against calling `call_from_thread` after dismissal.

---

## Design principles

- **Robustness > features.** Every external call (subprocess, HTTP) has a timeout + retry policy. Failures degrade gracefully with clear user-facing messages.
- **Non-fatal hooks.** Anything that talks across the VX/PM boundary is via subprocess and is non-fatal. If `copalvx` isn't on PATH, push/pull options stay greyed in the TUI but the rest of the tool works normally.
- **File-based persistence.** No database. `project.yaml` is the source of truth per project; the registry is an index; sessions.jsonl is an append-only log. Users can `cat` and `vim` their data.
