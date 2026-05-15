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

# 4. One-shot setup: background service + Finder Quick Actions
copalpm setup
```

### Windows

```powershell
# 1. Install uv (skip if already installed)
winget install astral-sh.uv

# 2. Install CopalPM
uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copalpm"

# 3. From an elevated terminal (Win+X -> Terminal (Admin)):
#    one-shot setup installs NSSM via winget, the background service,
#    and the Explorer right-click verbs.
copalpm setup
```

`copalpm setup` is idempotent — safe to re-run. It will skip whatever is
already in place. Reverse with `copalpm teardown` (also idempotent; user data
is preserved — see [Where your data lives](#where-your-data-lives)).

For partial installs, `copalpm setup --service-only` or `--shell-only` skips
the other half. The granular `copalpm service install` and
`copalpm shell-integration install` commands remain available for advanced use.

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

**macOS:** per-user install, no sudo required. The entries appear under
the Services menu (or right-click → Quick Actions).

**Windows:** install/uninstall need an **elevated terminal** (Win+X →
"Terminal (Admin)" → re-run the command). Status reads work as any user.

The verbs are written to HKLM because Windows 11 24H2/25H2 silently
filters per-user (HKCU) shell verbs added after the OS upgrade —
verified by side-by-side test. They only appear in the *legacy*
context menu: **Shift+right-click** on a folder, or right-click →
**"Show more options"** at the bottom of the modern menu. If they
still don't show, restart Explorer:
`taskkill /F /IM explorer.exe & start explorer.exe`.

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

## Three identifiers (Title vs ID vs CopalVX name)

Every project carries three names that serve different audiences and *can*
drift apart. The InitScreen shows a live preview (`ID: PROJ-<slug>-<date>  •
CopalVX: <slug>-<date>`) so you see all three before submitting.

| Name | Where it lives | Charset | Used for |
|------|----------------|---------|----------|
| **Title** | `project.yaml:name` and `name` in `registry.json` | Any Unicode (Greek, CJK, emoji) | Human display in the TUI; never sent to a server |
| **ID** | `project.yaml:id` and the registry key | ASCII only — `PROJ-<SLUG>-<DDMMYY>` (+ `_NNN` if the suffix box is ticked) | Stable internal handle; survives Title renames |
| **CopalVX name** | `project.yaml:copalvx.project_name` (post-push); folder basename until the first push | ASCII, server-unique | Server-side key for every version pushed via CopalVX |

Practical consequences:
- The folder basename **is** the CopalVX name on the first auto-push, so
  whatever the slug pipeline produces from your Title ends up on the server.
- Renaming on the CopalVX side (`[N]` in either TUI) updates only the
  CopalVX name — the Title and ID stay put.
- Non-ASCII Titles (e.g. Greek, CJK, accented Latin) are transliterated via
  `unidecode` before hitting the slug; pure-emoji Titles are rejected with a
  toast because they slug to nothing.

---

## Where your data lives

CopalPM and CopalVX each have their own per-user config directory. They are
**separate** — there's no shared file:

| Tool | macOS / Linux | Windows | What's in it |
|------|---------------|---------|--------------|
| **CopalPM** | `~/.config/copalpm/` | `%APPDATA%\copalpm\` | Project registry, session log, templates, service config |
| **CopalVX** | `~/.copal/config.json` | `%USERPROFILE%\.copal\config.json` | Server IP/port, client path, remembered pull destinations (`projects.json`) |

CopalPM's directory contents:
- `registry.json` — list of registered projects
- `sessions.jsonl` — append-only session log
- `current_session.json` — currently-running session (if any)
- `templates.json` — user-defined project templates
- `config.json` — service config (port, API key)

`copalpm teardown` removes the service and shell verbs but leaves both
directories untouched. To wipe CopalPM data, delete the directory above; to
wipe CopalVX client data, delete `~/.copal/`.
---

## Timezone behavior

CopalPM mixes two time conventions, which matters for distributed teams:

- **Sessions** (`sessions.jsonl`, `time_entries`, `current_session.json`) are
  stored in **UTC**. Both `copalpm time start/stop` and `copalpm time log`
  timestamp with `datetime.now(timezone.utc)`.
- **Deadlines** (`project.yaml:deadline`) are stored as plain `YYYY-MM-DD` and
  parsed at **local midnight**. The "X days remaining" display in
  `copalpm record show` and the TUI uses your machine's local date.

Practical consequences:
- Two collaborators in different timezones may see different "days remaining"
  on the same `YYYY-MM-DD` deadline (e.g. someone in UTC+11 ticks over a day
  earlier than someone in UTC-8).
- Session totals are timezone-stable: a session that crosses midnight local
  time still rolls up cleanly because it's UTC under the hood.
- If you re-export `time_entries` for invoicing, the UTC timestamps are
  authoritative — convert to your local zone at the report layer.

---

## License

Apache 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
