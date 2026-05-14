# CLAUDE.md — CopalPM

> AI-assistant orientation for the CopalPM package.
> For monorepo-wide context see [../CLAUDE.md](../CLAUDE.md).
> For user-facing install/usage see [README.md](./README.md).
> Last updated: 2026-05-14.

---

## What CopalPM is

Terminal project management + time tracking for motion design and VFX work. File-based — no database, no cloud. Each project is a folder containing `project.yaml`; the user's machine has a registry pointing at registered folders + a sessions log.

The single `copalpm` binary fronts everything via subcommand groups. With no args, it launches the TUI (the most common entry point).

Renamed from `ProjectRegistry` (and the package was `project_registry/`) in Phase 2 of the rebrand. The user data directory was migrated from `project-registry/` to `copalpm/` in a follow-up release with a one-time auto-copy on first run — see gotcha #1.

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
copalpm setup                      # one-shot install: service + shell integration
copalpm teardown                   # reverse of setup (user data preserved)

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

copalpm shell-integration install   # add Copal verbs to Explorer / Finder right-click
copalpm shell-integration uninstall # remove the OS shell verbs
copalpm shell-integration status    # show installed/missing state

copalpm deliver <path> [...]       # log a delivered asset
copalpm task-tracker               # daemon entry point (hidden — invoked by the OS service)
copalpm shell-trigger {start|stop|new-project} --folder PATH
                                   # internal verb handler (hidden — invoked by the OS shell)
```

`record` operates on the `project.yaml` in the CWD (walks up) or via `--file <path>` / `--project <id>`.

---

## Storage

Per-user data lives at:
- macOS / Linux: `~/.config/copalpm/`
- Windows: `%APPDATA%\copalpm\`

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

## Setup orchestration

`copalpm setup` (and its mirror `copalpm teardown`) live in `setup_cmd.py`.
They wrap `cmd_install_service` (pm.py) and `cmd_shell_install`
(shell_integration.py) under one umbrella with a single admin preflight on
Windows, idempotency probes (skip if already installed), per-step status
output, and a final summary. Step runners (`_do_service_install`,
`_do_shell_install`, etc.) return `(ok: bool, msg: str)` so the orchestrator
can render a uniform "[OK] / [FAILED] step-name: msg" line per step.

NSSM auto-install: if `nssm` isn't on PATH and the service step is in scope,
setup tries `winget install --silent NSSM.NSSM` once. Any failure (winget
absent, install error, or NSSM still not on PATH after install) downgrades
to a clear printed instruction rather than aborting. Skip flags (`--shell-only`,
`--skip-service`) bypass NSSM detection entirely.

The granular commands (`copalpm service install`, `copalpm shell-integration
install`) stay for users who want fine-grained control or who are diagnosing
a partial install. Setup just calls them in order.

Teardown removes the shell verbs **before** the service so users never see
a brief window where right-clicking surfaces a verb that points at a
just-removed daemon.

---

## Shell integration (Phase 6 F4)

`copalpm shell-integration install` adds three right-click verbs to the OS file
manager: "Copal: Start Timer", "Copal: Stop Timer", "Copal: New Project Here".
All three dispatch to the hidden `copalpm shell-trigger <verb> --folder PATH`
subcommand — that indirection keeps the OS-level command strings stable as the
underlying implementation evolves. Source: `shell_integration.py`.

Footprint:
- **Windows:** HKLM keys under `Software\Classes\Directory\shell\Copal*` (folder
  selected) and `Software\Classes\Directory\Background\shell\Copal*` (empty
  space inside a folder). Two contexts × three verbs = 6 parent keys. Each key
  carries a menu title, an `Icon` value pointing at `src/copalpm/assets/copal-*.ico`,
  and a `command` subkey. **Install/uninstall need admin elevation** — see
  gotcha #11 for why HKCU isn't viable on Win11 24H2+. Status is read-only
  and works for any user.
- **macOS:** `.workflow` bundles in `~/Library/Services/`. The XML for both
  `Info.plist` and `document.wflow` is generated from templates shipped at
  `src/copalpm/assets/macos_workflow/{Info.plist.template,document.wflow.template}`
  by substituting `__MENU_TITLE__` and `__COPALPM_COMMAND__` placeholders.
  The templates were captured from a real Automator-generated Quick Action —
  do **not** edit by hand. `AMWorkflowServiceRunner` aborts at runtime if the
  `workflowMetaData` structure isn't a recognized `AMServiceMetaData` shape
  (the Service appears in the Finder menu but clicking it silently does
  nothing — see gotcha #13). Install/uninstall runs `pbs -flush` so Finder
  picks up changes immediately.

The "New Project Here" verb spawns `copalpm tui --screen init --dir <folder>`
detached from the parent process; the TUI's `PMApp` accepts `initial_screen`
and `initial_dir` to deep-link straight to `InitScreen` with the folder
pre-filled. See `tui_app.py:PMApp.__init__` and `InitScreen.__init__`.

Icons are placeholder ICOs generated by `scripts/generate_icons.py` (one-shot,
Pillow). The script writes `copal{,-start,-stop,-new}.ico` to
`src/copalpm/assets/`; the wheel ships those files via
`[tool.hatch.build.targets.wheel.force-include]`. Maintainers regenerate them
when branding lands or glyphs change.

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

97 tests:
- 12 import tests (every module + handler resolves; no `from project_registry` references remain)
- 56 argparse tests (every documented subcommand invocation, required args, mutually-exclusive groups, hidden `task-tracker` and `shell-trigger`, the new `shell-integration` + `tui --screen` flags)
- 14 unit tests for shell_integration (verb definitions, asset resolution, Windows command-string quoting, macOS workflow XML well-formedness, notifier never raises)
- 7 integration tests for read-only ops (live binary spawn)
- 2 Windows-gated integration tests for the registry round-trip (skipped on macOS/Linux)

Integration tests auto-skip if the `copalpm` binary is not in the venv.

---

## Working with Claude (CopalPM-specific)

This package's gotchas (data-dir migration, HKLM/Win11 24H2 shell verbs, subprocess encoding, Textual scrollable-form layout) are codified in the `copal-gotcha-reviewer` subagent (defined at [../.claude/agents/copal-gotcha-reviewer.md](../.claude/agents/copal-gotcha-reviewer.md)) — invoke via `/copal-gotcha-check` after any change touching `src/copalpm/shell_integration.py`, `src/copalpm/config.py`, `src/copalpm/copalvx_api.py`, or any new file added under the user-data dir.

For cross-package contract changes (`copalvx_api.py`), run `/copal-cross-package` to verify both sides stay in sync.

See umbrella [../WORKFLOW.md](../WORKFLOW.md) for the full development protocol.

---

## Gotchas

1. **One-time auto-migration from the legacy `project-registry/` data dir.** `config.py:_resolve_data_dir()` runs on every import: if `<base>/copalpm/` doesn't exist but `<base>/project-registry/` does, it `shutil.copytree`s the legacy directory into the new location and writes a `.migrated_from_project-registry` marker (source path + UTC timestamp). The legacy directory is preserved as a backup — users delete it manually once they've verified things work. If the copy fails (permissions, disk full, etc.) the resolver falls back to the legacy directory so the tool stays functional; next run retries. Idempotent: once the new dir exists, the migration code is a no-op. Note: if the task-tracker service was running OLD code during the upgrade, it will keep writing to `project-registry/` while CLI processes use `copalpm/` — users running the daemon must `copalpm service uninstall && copalpm service install` after upgrade.

2. **`task-tracker` is hidden from `copalpm --help` but still callable.** The OS service spec invokes `copalpm task-tracker`. `cli._build_parser()` filters it out of `_choices_actions` after construction (argparse's `help=SUPPRESS` leaves an ugly `==SUPPRESS==` literal in `--help` output; the filter cleans that up).

3. **Pre-rebrand service installs must be uninstalled before installing the new one.** Old plist label was `com.projectregistry.task-tracker` / NSSM service was `TaskTracker`. The new commands won't touch the old service entries. Anyone migrating must run `pm uninstall-service` from the old install before `copalpm service install` from the new one — otherwise the old service keeps trying to invoke a `task-tracker` binary that no longer exists.

4. **Daemon spec changed in Phase 2.** Before: standalone `task-tracker(.exe)` binary registered directly with the OS service manager. After: the `copalpm` binary is registered, with `task-tracker` as the first argument. macOS plist `ProgramArguments` is a 2-element array; NSSM install passes `task-tracker` as the service args. Reflected in `pm.py`'s `cmd_install_service`.

5. **`call_from_thread()` is on `App`, not `Screen`.** In Textual, use `self.app.call_from_thread()` not `self.call_from_thread()` when calling from a background thread inside a Screen subclass. Multiple places in `tui_app.py` rely on this pattern (e.g. `DashboardScreen`'s 60s server-version poll).

6. **Textual scrollable centered forms have a fragile layout pattern.** `ScrollableContainer(Vertical(...))` with `height: 1fr` collapses the inner `Vertical` to zero. Correct pattern: `Vertical(id="box")` as the direct child of the Screen with **`height: 85vh`** (fixed — `height: auto` breaks `vh`/`1fr` resolution in children); flat `ScrollableContainer(id="scroll")` inside with **`height: 1fr`**; all form fields as flat direct children of the `ScrollableContainer`; buttons outside, below it. See `InitScreen` and `EditTemplateModal` for the canonical implementation. **Never nest a `Vertical` inside a `ScrollableContainer` to group toggle-able fields** — it clips virtual height; use a CSS class instead and `query(".class")` for bulk display toggling.

7. **Subprocess stdout encoding defaults to cp1252 on Windows when piped.** Emoji in print statements causes `UnicodeEncodeError: 'charmap' codec can't encode character`. Fix in any code that spawns Python subprocesses on Windows: pass `PYTHONIOENCODING=utf-8` in the subprocess environment. Also pass `PYTHONUNBUFFERED=1` if you need real-time line streaming from the child process.

8. **Background threads in `DashboardScreen` must use `self.app.call_from_thread()`.** DashboardScreen is the root screen and never gets popped, so holding `self` in a daemon thread is safe. Other screens that can be popped should avoid long-lived threads or guard against calling `call_from_thread` after dismissal.

9. **HTTP calls must never run on the 1s tick of `_tick_timer`.** Both `DashboardScreen` and `ProjectDetailScreen` previously called `_active_session()` (an HTTP GET to the task-tracker daemon, 2s timeout) directly inside `_tick_timer`, which fires every second on the render thread. When the daemon was down — common after the Phase 2 service rename if a user hadn't reinstalled the service — every tick blocked for up to 2 seconds, producing severe scroll lag and periodic stutters. Pattern in place now: a daemon thread polls every 5s and writes to `self._session_cache`; `_tick_timer` just reads the cache and formats the title. Any future "watch this remote thing" code must follow the same shape.

10. **The `📁` folder picker on InitScreen requires `textual-fspicker`.** Adding new path inputs anywhere in the TUI? Use the same pattern — `Horizontal(Input, Button("📁"))` with a button handler that calls `self.app.push_screen(SelectDirectory(<start>), <callback>)`. The picker's starting location walks up the filesystem to the nearest existing path; absent that, it falls back to `Path.home()`. See `InitScreen._open_dir_picker` for the canonical implementation.

11. **Windows 11 24H2/25H2 silently filters per-user shell verbs.** The first cut wrote verbs to `HKCU\Software\Classes\Directory(\Background)\shell\…` — works on Win10 and pre-24H2 Win11, doesn't work on Win11 build 26200+. Verified by side-by-side test: an HKLM-registered minimal verb appeared in the legacy menu, an HKCU one with identical structure (same default value, same ACL, same parent class) did not. Pre-existing HKCU verbs (e.g. Anchorpoint installed before the OS upgrade) are grandfathered in; new HKCU writes after the upgrade are filtered. The fix is to write to HKLM, which requires admin (`copalpm shell-integration install` checks `IsUserAnAdmin()` and prints clear instructions if not elevated). `_uninstall_windows()` only requires admin if there are HKLM keys to remove — keeping the cleanup of stale HKCU entries from older installs admin-free. Verbs only appear in the *legacy* context menu — Shift+right-click, or right-click → "Show more options"; the modern menu requires an IExplorerCommand COM extension which is out of scope for F4. Explorer also caches verb visibility per-user; if newly installed verbs don't show, `taskkill /F /IM explorer.exe & start explorer.exe` (or sign out / sign in for the most stubborn cases). The Finder `pbs -flush` call in the macOS installer makes the equivalent issue invisible on the Mac.

12. **`time_cli._api()` now raises instead of `sys.exit`.** The HTTP client used to call `sys.exit(1)` on `URLError`. After F4 the failure modes are exposed as `ServiceDownError` and `ApiError` so the hidden `shell-trigger` handler can render a toast notification. CLI handlers (`cmd_start`, `cmd_stop`) are wrapped in the `_exit_on_service_error` decorator to keep the original exit-on-error behavior. `cmd_status` catches both exceptions directly and prints a soft "service not running" line — same UX as before. Any new caller of `_api()` from outside the CLI surface must handle these two exception types.

13. **macOS `.workflow` XML is hostile to hand-rolling.** The first F4 cut generated `document.wflow` programmatically from a small Python f-string. `pbs` registered the Service and Finder showed the menu item, but clicking did nothing — `WorkflowServiceRunner` would crash with `'Workflow's metaData should be service metaData!'` at `AMWorkflowServiceRunner.m:330`. The runtime expects `workflowMetaData` to look like a fully populated `AMServiceMetaData` dict (with `applicationBundleID`, `applicationPath`, `presentationMode`, `serviceApplicationBundleID`, `serviceProcessesInput`, etc.) and the action dict to carry the full `arguments` mapping that Automator emits. We now ship templates captured from a real Automator-saved Quick Action and substitute only the menu title + the shell command. If you need to update the templates, build a fresh Quick Action in Automator on a Mac and copy `document.wflow` / `Info.plist` over the existing files; do not hand-tune.

---

## Design principles

- **Robustness > features.** Every external call (subprocess, HTTP) has a timeout + retry policy. Failures degrade gracefully with clear user-facing messages.
- **Non-fatal hooks.** Anything that talks across the VX/PM boundary is via subprocess and is non-fatal. If `copalvx` isn't on PATH, push/pull options stay greyed in the TUI but the rest of the tool works normally.
- **File-based persistence.** No database. `project.yaml` is the source of truth per project; the registry is an index; sessions.jsonl is an append-only log. Users can `cat` and `vim` their data.
