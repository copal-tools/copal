# CopalPM

A lightweight, file-based project management and time-tracking system for media/VFX pipelines. No database, no cloud — plain files, a unified CLI, a Textual TUI, and a local HTTP service for active session tracking.

Companion to [CopalVX](https://copalvx.com) (content-addressable version exchange) — the two pair for a complete studio asset + project workflow.

> **Renamed from `ProjectRegistry` in May 2026.** The 6 separate commands (`pm`, `project`, `tt`, `task-tracker`, `deliver`, `pm-tui`) are now subcommand groups under a single `copalpm` binary.

---

## Install

### Mac

```bash
# 1. Install uv (skip if already installed)
brew install uv

# 2. Add uv tools to PATH (one-time shell config)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# 3. Install CopalPM
uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copalpm"

# 4. Install and start the background time-tracking service
copalpm service install
```

### Windows

```powershell
# 1. Install uv (skip if already installed)
winget install astral-sh.uv

# 2. Install CopalPM
uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copalpm"

# 3. Install NSSM (required for the time-tracking service)
winget install NSSM.NSSM

# 4. Install and start the background time-tracking service (Admin PowerShell)
copalpm service install
```

### Update

```bash
uv tool upgrade copalpm
```

---

## CLI surface

`copalpm` is a single binary with subcommand groups. Run with no args to launch the TUI.

```
copalpm                            # launch TUI dashboard

copalpm project init <name>        # create + register a new project
copalpm project list               # list registered projects
copalpm project status [--json]    # summary table of all projects
copalpm project register <path>    # register an existing folder
copalpm project scan <dir>         # find + register projects in a tree
copalpm project remove <id>        # remove from registry
copalpm project rollup [--json]    # total time per project

copalpm record show                # pretty-print this project's record
copalpm record get <field>         # read field from project.yaml
copalpm record set <field> <val>   # write field to project.yaml
copalpm record phase <phase>       # log a phase transition
copalpm record validate            # schema check
copalpm record sync-time           # flush sessions.jsonl into time_entries

copalpm time start [desc]          # start tracking session
copalpm time stop                  # stop current session
copalpm time status                # show current session
copalpm time log <min> <desc>      # manually log time

copalpm service install            # install task-tracker background service
copalpm service uninstall          # remove the service
copalpm service status             # service state

copalpm shell-integration install   # add Copal verbs to right-click menus
copalpm shell-integration uninstall # remove the OS shell verbs
copalpm shell-integration status    # show installed verbs

copalpm deliver <path> [...]       # log a delivered asset
```

`copalpm record` operates on the project.yaml in the current directory (walks up) or a specific record via `--file <path>` / `--project <id>`.

---

## Background service

The task-tracker daemon runs locally on `127.0.0.1:5123` and tracks live time sessions. It starts automatically on login after `copalpm service install`. On macOS it runs via launchd; on Windows it runs as a service installed through NSSM.

```bash
copalpm service install      # install + start
copalpm service uninstall    # stop + remove
copalpm service status       # is the service running?
```

---

## Explorer / Finder integration (optional)

Add three right-click verbs to your file manager so you can start a timer,
stop a timer, or start a new project without leaving the OS shell:

```bash
copalpm shell-integration install     # add the verbs
copalpm shell-integration status      # show what's installed
copalpm shell-integration uninstall   # remove them
```

The verbs work on any folder:

- **Copal: Start Timer** — starts a time session against the project whose
  `project.yaml` lives at or above the selected folder. Toast notification on
  success; if the background service isn't running, you'll see an error
  pointing at `copalpm service install`.
- **Copal: Stop Timer** — stops the current session.
- **Copal: New Project Here** — opens the CopalPM TUI directly on its
  New Project screen with the folder pre-filled.

Per-user install — no admin / sudo required. On Windows the entries live
in HKCU and may not appear until Explorer is restarted. On macOS the
entries appear under the Services menu (or right-click → Quick Actions).

---

## Integration with CopalVX

CopalVX (`copalvx push/pull`) automatically invokes CopalPM hooks before/after each push or pull, if `copalpm` is installed and a `project.yaml` is found:

| Event | Action |
|-------|--------|
| pre-push | Flushes pending time sessions into `project.yaml` so time travels with the push |
| post-push | Stamps `project.yaml` with the CopalVX project name + version tag pushed |
| post-pull | Registers the pulled folder in the CopalPM registry + shows the CopalVX block |

The integration is opt-in: it only activates when `copalpm` is on PATH. CopalVX continues to work standalone if CopalPM is not installed.

You can also push/pull from the CopalPM TUI directly (project detail screen → `p` / `l` keys).

---

## Storage

User data lives under:

- macOS / Linux: `~/.config/copalpm/`
- Windows: `%APPDATA%\copalpm\`

> **Upgrading from a pre-rebrand install?** On first run, any data in `project-registry/` is automatically copied to `copalpm/`. The old directory is preserved as a backup — delete it manually once you've verified everything works. If the task-tracker service was running during the upgrade, restart it (`copalpm service uninstall && copalpm service install`) so it picks up the new path.

Contents:
- `registry.json` — list of registered projects
- `sessions.jsonl` — append-only session log
- `templates.json` — user-defined project templates
- `config.json` — service config (port, API key)

---

## License

Apache 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
