# CLAUDE.md — CopalBlender

> AI-assistant orientation for the CopalBlender package.
> For monorepo-wide context see [../CLAUDE.md](../CLAUDE.md).
> For user-facing install/usage see [README.md](./README.md).
> Last updated: 2026-05-16.

---

## What CopalBlender is

A Blender addon that auto-starts CopalPM time tracking for the project a `.blend` file belongs to. It ships in two halves:

1. **The `copalblender` CLI** — a small Python package (`src/copalblender/`) that installs, uninstalls, and probes the addon across every Blender version on the host OS.
2. **The Blender addon itself** — `src/copalblender/assets/addon/copal_blender/`, copied verbatim into each Blender's `scripts/addons/copal_blender/` at install time.

The addon never imports the `copalblender` package — Blender ships its own bundled Python that can't see the host's site-packages. The addon vendors a minimal subprocess + HTTP client so it stands alone inside Blender.

---

## Layout

```
copalblender/
├── pyproject.toml                  # name=copalblender, script=copalblender, py>=3.12
├── src/copalblender/
│   ├── cli.py                      # argparse: `copalblender install|uninstall|status`
│   ├── installer.py                # detect installs, copy addon, remove addon, probe state
│   ├── platform_paths.py           # per-OS CopalPM config + Blender user-config root resolution
│   └── assets/addon/copal_blender/ # The Blender addon — copied verbatim on install
│       ├── __init__.py             # bl_info, register(), unregister(), handler + timer wiring
│       ├── tracker.py              # Pure-function state machine: events → actions
│       ├── copalpm_client.py       # Vendored: whose() subprocess + HTTP _api(), ServiceDownError/ApiError
│       ├── activity.py             # OS-level cursor position + foreground-window probes
│       ├── preferences.py          # bpy.types.AddonPreferences (intervals, thresholds, path override)
│       └── status_panel.py         # 3D-View N-panel showing current session + manual stop
└── tests/
    ├── unit/
    │   ├── conftest.py             # Adds the addon source dir to sys.path so tests can import its modules
    │   ├── test_platform_paths.py
    │   ├── test_installer_detect.py
    │   ├── test_installer_copy.py
    │   ├── test_copalpm_client_whose.py
    │   ├── test_copalpm_client_http.py
    │   └── test_tracker_state_machine.py
    └── integration/
        └── test_cli_smoke.py       # Spawn `copalblender status` against synthesized version dirs
```

The addon's pure-Python modules (`tracker.py`, `copalpm_client.py`, `activity.py`) MUST NOT `import bpy` at module scope — that's what makes them unit-testable from the host's pytest. Anything that genuinely needs `bpy` (handlers, timers, preferences UI, panel) lives in `__init__.py`, `preferences.py`, and `status_panel.py`, which the unit tests do not import.

---

## CLI surface

```
copalblender install      # install addon into every detected Blender version's scripts/addons/
copalblender uninstall    # remove from every detected version
copalblender status       # list each detected version + whether the addon is installed
```

Exit codes: 0 on full success; 1 if any version failed or no installs were detected.

---

## Architecture

```
┌────────────────────────────┐
│   Blender (bundled Python) │
│  ┌─────────────────────┐   │
│  │  copal_blender/     │   │
│  │   __init__.py       │   │   handlers + timer + atexit
│  │   preferences.py    │   │   AddonPreferences
│  │   tracker.py        │◀──┼── pure state machine
│  │   activity.py       │   │   cursor + focus (OS-native)
│  │   copalpm_client.py │   │   subprocess + HTTP
│  └──────────┬──────────┘   │
└─────────────┼──────────────┘
              │
              ▼
   ┌──────────────────────┐         ┌──────────────────────┐
   │  copalpm whose --json│         │ CopalPM daemon       │
   │  (subprocess)        │         │ 127.0.0.1:5123       │
   └──────────────────────┘         │  /start /stop /ping  │
                                    │  /state /health      │
                                    └──────────────────────┘
```

### Why a vendored client

The Blender addon runs under Blender's bundled Python. We can't import the `copalblender` package or any third-party library from the host's site-packages. The addon ships its own minimal client (`copalpm_client.py`) using only the standard library: `subprocess` for `whose`, `urllib.request` for HTTP, `json` for parsing.

The vendored client mirrors [copalpm/src/copalpm/time_cli.py:49-80](../copalpm/src/copalpm/time_cli.py) `_api` exactly:
- `ServiceDownError` on `urllib.error.URLError` (daemon unreachable)
- `ApiError(code, message)` on non-2xx HTTP responses
- A separate `NotInstalledError` for the "copalpm not on PATH" case (relevant only to the addon — the CLI client raises through to the user)

### State machine

`tracker.py` exposes a single pure function:

```python
def handle_event(event, ctx) -> list[action]
```

- `event` is a tuple: `("file_loaded", path)`, `("file_closing", new_path)`, `("file_saved", path)`, `("tick", now)`, `("quit",)`.
- `ctx` is a `SimpleNamespace` with `client`, `prefs`, `cursor_pos`, `is_focused`, `now`, `current_filepath`.
- Actions are tuples: `("start", project_id)`, `("stop", reason)`, `("ping",)`.

State is held in a module-level dict mutated only by `handle_event`. The Blender side dispatches actions through `copalpm_client`.

This split makes the entire decision logic unit-testable without touching `bpy`, and it keeps `__init__.py` thin and obviously correct.

### Activity probes

`activity.py` exposes two functions:

```python
def get_cursor_pos() -> tuple[int, int] | None
def is_blender_focused() -> bool | None
```

Each returns `None` when the platform can't answer. The tracker treats `None` as "skip that check" — partial functionality never blocks the rest.

| Platform | Cursor | Focus |
|----------|--------|-------|
| Windows | `ctypes` `GetCursorPos` | `ctypes` `GetForegroundWindow` + `GetWindowThreadProcessId` compared to `os.getpid()` |
| macOS | `Quartz.CGEventSourceCounterForEventType(kCGEventMouseMoved)` (PyObjC) → fallback to `ioreg HIDIdleTime` (idle seconds) | `osascript` System Events frontmost-process |
| Linux X11 | `xdotool getmouselocation --shell` | `xdotool getactivewindow getwindowname` matched to "Blender" |
| Linux Wayland | `None` | `None` |

When PyObjC isn't available inside Blender's bundled Python on macOS (the common case), `get_cursor_pos()` falls back to "idle seconds since last cursor movement." The cursor-static check becomes "idle ≥ ping interval" — functionally equivalent for the user-facing behaviour.

---

## Storage / configuration

The addon reads CopalPM's daemon config (`api_key`, `port`) from CopalPM's data dir, matching [copalpm/src/copalpm/config.py:11-17](../copalpm/src/copalpm/config.py):

| OS | Path |
|----|------|
| Windows | `%APPDATA%\copalpm\config.json` |
| macOS | `~/.config/copalpm/config.json` |
| Linux | `~/.config/copalpm/config.json` |

If the file is missing, `_load_pm_config()` raises `NotInstalledError`; the tracker's dispatch loop catches it silently. The addon does not write to this directory — it's CopalPM's exclusive territory.

---

## Tests

```powershell
uv sync --directory copalblender                    # one-time
uv run --directory copalblender pytest              # all tests
uv run --directory copalblender pytest tests/unit/  # unit only (no Blender required)
```

Unit tests import the addon's pure-Python modules directly. `tests/unit/conftest.py` adds `src/copalblender/assets/addon/copal_blender/` to `sys.path` so `import tracker`, `import copalpm_client`, etc. all work.

The integration test (`test_cli_smoke.py`) spawns `copalblender status` against fake Blender version directories built in `tmp_path` — it auto-skips if the `copalblender` script isn't on PATH inside the venv.

There is no automated test for the Blender side — that requires Blender. Run the manual verification plan in [the implementation plan](../.claude/plans/we-are-going-idempotent-valiant.md) or the README's troubleshooting section.

---

## Gotchas

1. **Don't `import bpy` at module scope in `tracker.py`, `copalpm_client.py`, or `activity.py`.** These modules are imported from pytest (host Python, no bpy). Anything that genuinely needs `bpy` belongs in `__init__.py`, `preferences.py`, or `status_panel.py`.

   ↪ **Corollary — don't touch `bpy.data` or most of `bpy.context` inside `register()`.** Blender wraps both in `_RestrictData` / `_RestrictContext` during addon registration, and `bpy.data.filepath` raises `'_RestrictData' object has no attribute 'filepath'`. Defer any work that needs `bpy.data` to a one-shot timer scheduled at the end of `register()` — by the time the timer body runs, the restriction has lifted. See `_fire_initial_event` in `__init__.py` for the pattern.

   ↪ **Corollary 2 — `register_class` must be idempotent.** When `register()` itself raises partway through (e.g. the `bpy.data` access above), the classes it has already registered stay registered. The next time the user toggles the addon, plain `bpy.utils.register_class(MyClass)` raises `ValueError: register_class(...): already registered as a subclass 'MyClass'`. Wrap registration in a `try/except ValueError → unregister_class → register_class` block (see `_safe_register_class` in `__init__.py` and the panel registration in `status_panel.py`). This lets the user recover by simply re-toggling the addon, no Blender restart required.

2. **Subprocess encoding on Windows.** Inherits CopalPM's gotcha #6 — pass `PYTHONIOENCODING=utf-8` in the subprocess `env` when calling `copalpm whose`. Without it, the addon crashes on any project name containing non-ASCII characters.

3. **Blender on macOS launched from Finder has a stripped PATH.** `shutil.which("copalpm")` may return None even when `copalpm` is installed. `whose()` resolves the binary in order: `shutil.which` → addon preferences override → hardcoded fallback list (`~/.local/bin/copalpm`, `/usr/local/bin/copalpm`, `/opt/homebrew/bin/copalpm`). Document this in the README's troubleshooting section so users know to set the override.

4. **Blender has no `exit_pre` handler.** Use `atexit.register()` in `register()`. Caveat: `atexit` doesn't fire on hard kill (SIGKILL, power loss, blue screen). The CopalPM daemon's 20-minute idle auto-stop covers those cases.

5. **Blender's main-thread timer is the right place for HTTP calls.** Use `bpy.app.timers.register(_tick, ...)`. Do NOT spawn a `threading.Thread` — `bpy` is not thread-safe. The timer runs on Blender's event loop with the GIL; a 5-second `urlopen` timeout is fine because the timer fires at most every `ping_interval_sec` (default 60s).

6. **`_api()` raises instead of `sys.exit`** (inherits CopalPM gotcha #11). The dispatch loop in `__init__.py` catches `ServiceDownError`, `NotInstalledError`, and `ApiError` separately. Never let any of these propagate to Blender or it'll show an unhelpful red error in the system console.

7. **`load_pre` may fire before the new file is on disk.** When the user picks a "Recent File" entry, `load_pre` fires with the *intended* path; the file may not exist yet. The tracker treats `("file_closing", path)` as a no-op and resolves the project entirely in the subsequent `load_post` event. Don't add side-effects to the `file_closing` branch.

8. **The default startup scene has `bpy.data.filepath == ""`.** Treat empty filepath as "untitled, no project". The tracker's `("file_loaded", None)` branch stops any active session and returns no further actions.

9. **Blender addon directory layout matters.** The addon must live as a directory under `<blender>/scripts/addons/copal_blender/` with an `__init__.py` containing `bl_info`. A single-file addon would also work but breaks our multi-module structure.

10. **`uv tool install` puts copalblender into `~/.local/share/uv/tools/copalblender/` on Linux/macOS and `%LOCALAPPDATA%\uv\tools\copalblender\` on Windows.** The shim binary lands in `~/.local/bin/` or `%LOCALAPPDATA%\Microsoft\WindowsApps\` respectively — same as `copalpm`. Verify both are on PATH after install; same gotcha applies for both packages.

---

## Working with Claude

This package follows the umbrella `WORKFLOW.md` conventions. Slash commands:

- `/copal-test copalblender` — runs `uv run --directory copalblender pytest`.
- `/copal-gotcha-check` — currently doesn't have copalblender-specific rules; the existing storage-contract / encoding / HKLM gotchas don't apply here. Re-evaluate if we ever add HKCU/HKLM writes (we don't).
- `/copal-doc-check` — covers this CLAUDE.md too.

Cross-package contract: any change to the daemon's `/start`, `/stop`, `/ping`, `/state`, or `/health` endpoints in [copalpm/src/copalpm/task_tracker.py](../copalpm/src/copalpm/task_tracker.py) must be mirrored in [src/copalblender/assets/addon/copal_blender/copalpm_client.py](./src/copalblender/assets/addon/copal_blender/copalpm_client.py) **and** in [copalpm/src/copalpm/time_cli.py](../copalpm/src/copalpm/time_cli.py) (the CopalPM-side client). Add a test that pins the exact endpoint paths if you find this contract drifting in review.

---

## Template for future DCC plugins

`copalblender` is the reference implementation for any DCC-side integration that wants to drive CopalPM time tracking. When the next plugin lands (Maya, Houdini, Nuke, ...), copy this shape unless you have a specific reason not to.

### 1. Package shape — sibling at the monorepo root

`copal{dcc}/` next to `copalvx/`, `copalpm/`, `copalblender/`. Own `pyproject.toml`, `LICENSE`, `NOTICE`, `README.md`, `CLAUDE.md`. Standalone-installable: `uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copal{dcc}"`. No umbrella `copaldcc/` package yet — revisit when the second DCC plugin has lived in `main` for a release cycle and the duplicated `copalpm_client.py` actually causes pain.

### 2. Two-half packaging — thin host CLI + bundled DCC plugin

Every DCC ships its own Python (Blender's bundled CPython, Maya's mayapy, Houdini's hython, Nuke's nukepython). That Python can't see the host's `site-packages`. So:

* **Host side** (`src/copal{dcc}/`) — a small Python package installed normally. Provides `copal{dcc} install | uninstall | status`. Imports `importlib.resources`, `shutil`, `pathlib`. Unit-testable from pytest.
* **DCC side** (`src/copal{dcc}/assets/addon/copal_{dcc}/`) — the actual plugin code. Bundled as a wheel data file via:
  ```toml
  [tool.hatch.build.targets.wheel.force-include]
  "src/copal{dcc}/assets" = "copal{dcc}/assets"
  ```
  The CLI's `install` command copies this folder verbatim into the DCC's plugin directory using `shutil.copytree(src, dst, dirs_exist_ok=True)`.

### 3. Three-command CLI surface

```
copal{dcc} install      # copy plugin into every detected DCC version's plugin dir
copal{dcc} uninstall    # remove from every detected version
copal{dcc} status       # list each detected version + whether plugin is present
```

Exit code: 0 if all targets succeeded, 1 if any failed or no DCC installs were detected. The "no installs detected" branch prints to stderr: `"error: No {DCC} installs detected. Open {DCC} at least once to create the user config directory."`.

### 4. `platform_paths.py` — per-OS DCC location + CopalPM config

Each DCC has its own per-version user config root. The pattern: one function `{dcc}_user_config_root()` that branches on `platform.system()`, plus `list_{dcc}_versions(root)` that filters subdirs against a version regex. Mirror copalblender's existing pattern; `copalpm_config_path()` is identical across all DCC plugins and worth duplicating verbatim (it has to track [copalpm/src/copalpm/config.py:11-17](../copalpm/src/copalpm/config.py)).

### 5. Vendored `copalpm_client.py`

Every DCC plugin needs its own copy. Stdlib only — `subprocess`, `urllib.request`, `json`, `shutil`, `os`, `platform`, `pathlib`. Mirror `time_cli._api` semantics exactly:

* `ServiceDownError` — `urllib.error.URLError` (daemon unreachable)
* `ApiError(code, message)` — non-2xx HTTP response, with `error` or `hint` field from JSON body when present
* `NotInstalledError` — DCC-plugin-only; raised when `copalpm` isn't on PATH or `<copalpm-data-dir>/config.json` is missing

Resolution order for the `copalpm` binary: `shutil.which("copalpm")` → addon preference override → hardcoded per-OS fallback list (`~/.local/bin/copalpm`, `/usr/local/bin/copalpm`, `/opt/homebrew/bin/copalpm` on Unix; `%LOCALAPPDATA%\Microsoft\WindowsApps\copalpm.exe` etc. on Windows). Always set `PYTHONIOENCODING=utf-8` in the subprocess `env`.

### 6. Pure-function `tracker.py`

The decision logic. NO DCC API imports at module scope — this is the file you unit-test from host pytest. Shape:

```python
def handle_event(event: tuple, ctx) -> list[tuple]: ...
def reset_state() -> None: ...

state = {"tracking_project_id": ..., "last_cursor": ..., "cursor_static_run": ..., "unfocus_since": ...}
```

Events:

* `("file_loaded", path)` — DCC just opened a file. Resolve project via `whose`, start/stop/no-op.
* `("file_closing", new_path)` — DCC's pre-load hook. No-op; let the subsequent `file_loaded` resolve.
* `("file_saved", path)` — re-resolve in case of Save-As into a different project folder.
* `("tick", monotonic_seconds)` — periodic timer. Active branch: ping + cursor-static check + unfocus check. Idle branch: detect cursor resume → re-start if file is still in a project.
* `("quit",)` — DCC exiting; flush a final stop.

Actions: `("start", project_id)`, `("stop", reason)`, `("ping",)`. The dispatcher in the DCC-side wiring file translates each into `client.start/stop/ping`.

### 7. `activity.py` — OS probes with the None contract

Two functions: `get_cursor_pos() -> tuple[int, int] | None`, `is_{dcc}_focused() -> bool | None`. Each returns either a concrete value OR `None` when the platform can't answer. The tracker treats `None` as "skip this check". This makes Wayland and PyObjC-less macOS Blenders work in degraded but functional mode.

Implementations to copy verbatim:

* **Windows** — `ctypes.windll.user32.GetCursorPos` for cursor; `GetForegroundWindow` + `GetWindowThreadProcessId` compared to `os.getpid()` for focus. Always works.
* **macOS** — try `Quartz.CGEventCreate` / `CGEventGetLocation`; fall back to `None` (no usable mouse-position fallback shipped). Focus via `osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true'` and string-match the DCC name.
* **Linux** — `xdotool getmouselocation --shell` for cursor; `xdotool getactivewindow getwindowname` + string match for focus. X11 only; both return `None` on Wayland.

### 8. DCC-side wiring file (`__init__.py` or DCC's equivalent entry point)

The ONLY file that imports the DCC API. Contains:

* `bl_info` (or DCC equivalent metadata)
* Adapter class with `whose(path)` that threads the path-override preference through to the vendored client
* `_run(event)` helper that builds a `ctx`, runs `tracker.handle_event`, and dispatches actions
* `_dispatch_action(action)` catching `ServiceDownError`, `NotInstalledError`, `ApiError` distinctly
* File-event handlers: load_post, load_pre, save_post
* Periodic timer body (`_tick`)
* `atexit.register(_on_quit)` for clean exit
* `register()` / `unregister()` — guarded by `_safe_register_class` and using a one-shot timer for any work that needs DCC state right after registration (see gotchas 1, 4)

### 9. Preferences

DCC-specific class with at minimum:

```
enabled: bool                    # master switch
ping_interval_sec: int           # default 60
unfocus_stop_sec: int            # default 300
cursor_static_pings: int         # default 2
copalpm_path_override: str       # for stripped-PATH cases
```

The tracker reads these from `ctx.prefs` each tick. Changes take effect on next tick — no restart needed.

### 10. Manual control UI

A small panel (Blender N-panel / Maya shelf button / Houdini shelf tool / Nuke panel) with **Start** and **Stop** operators. Both:

* Have `poll()` methods to grey out at the right time
* Catch all three exception types and surface as DCC-native warnings/toasts
* Update `tracker.state` in lockstep with the daemon call so the periodic tick stays in sync

Stop additionally clears `tracker.state["last_cursor"] = None` to give the idle branch one baseline tick before it can auto-restart on cursor movement.

### 11. Test split

* **Pure-Python sides** (`tracker`, `copalpm_client`, `activity`, `platform_paths`, `installer`, `cli`) — full pytest coverage from host Python. Use `tmp_path` for filesystem fixtures; monkeypatch `subprocess.run`, `urllib.request.urlopen`, `platform.system`, `os.environ` as needed.
* **DCC-side wiring** — can't be unit-tested without the DCC. Document a manual test plan in the package's CLAUDE.md (file load / save / close, tick scenarios, quit).
* **Integration smoke** — spawn the `copal{dcc}` binary against synthesized fake DCC version directories built in `tmp_path`. Inject `APPDATA` (Win) or `HOME` (Unix) env vars to redirect the binary's view of the user config tree.

A `tests/unit/conftest.py` shim adds the bundled addon source dir to `sys.path` so tests can `import tracker`, `import copalpm_client` directly. NO duplication: tests run against the same source tree the wheel ships.

### 12. Cross-package contract

Add the package to the cross-package mirror list. Any change to copalpm's HTTP endpoints in [copalpm/src/copalpm/task_tracker.py](../copalpm/src/copalpm/task_tracker.py) must be mirrored in BOTH:

* [copalpm/src/copalpm/time_cli.py](../copalpm/src/copalpm/time_cli.py) (CopalPM's own client)
* `copal{dcc}/src/copal{dcc}/assets/addon/copal_{dcc}/copalpm_client.py` (this DCC's vendored copy)

Update the umbrella [CLAUDE.md](../CLAUDE.md) "Cross-package contract" bullet to include the new DCC. Future work: add a contract-pinning test under `tests/integration/` that fires every documented endpoint against a stub daemon and asserts the request shape — drift detection in CI.

### 13. Suggested implementation order

Each step ends in something independently verifiable.

1. Package skeleton — pyproject, LICENSE/NOTICE/README/CLAUDE.md stubs, hatch `force-include` of `src/copal{dcc}/assets`. Verify: `uv sync && copal{dcc} --help`.
2. `platform_paths.py` + tests.
3. `installer.py` + tests.
4. Wire `cli.py` end-to-end. Verify: `copal{dcc} status` on the dev box.
5. Skeleton DCC plugin: `bl_info` (or equivalent) + empty `register()` that just prints. Manually load in the DCC.
6. Vendored `copalpm_client.py` + tests.
7. `activity.py` + tests.
8. `tracker.py` state machine + tests — every spec scenario.
9. Wire DCC handlers + timer + atexit. Manually verify file load triggers tracking.
10. `preferences.py`.
11. Manual control panel (Start/Stop buttons).
12. Documentation — `CLAUDE.md` (architecture, gotchas, manual test plan), `README.md`. Update umbrella `CLAUDE.md`, `MERGE_PLAN.md`, root `README.md`, and `copalpm/CLAUDE.md` "Integration with copal{dcc}" sub-section.
13. Manual verification pass.

### 14. Gotchas to port forward

Each DCC will have its own quirks, but these from copalblender apply to most:

* **`register()` restrictions** — many DCC plugin systems wrap their global API in a sandbox during plugin load. Defer anything that touches that API to a one-shot timer.
* **Idempotent `register_class`** — when the first `register()` partially fails, classes stay registered. Wrap registration to recover via unregister-then-register.
* **DCC's bundled Python is sandboxed** — never assume host libraries are importable. Vendor what you need; stdlib only when possible.
* **Subprocess encoding on Windows** — `PYTHONIOENCODING=utf-8` in subprocess env.
* **macOS launched from Finder has a stripped PATH** — preference override + hardcoded fallback list.
* **No `exit_pre` in most DCCs** — use `atexit.register()`. Document that hard kills are caught by copalpm's 20-min daemon-side idle auto-stop.
* **HTTP calls must run on the DCC's main thread** — use the DCC's main-loop timer, not `threading.Thread`. Most DCC APIs are not thread-safe.
