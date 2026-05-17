# CLAUDE.md — Copal Tools (monorepo umbrella)

> Read this first for orientation across the monorepo.
> Per-package detail lives in [copalvx/CLAUDE.md](./copalvx/CLAUDE.md), [copalpm/CLAUDE.md](./copalpm/CLAUDE.md), and [copalblender/CLAUDE.md](./copalblender/CLAUDE.md).
> Last updated: 2026-05-17 (CopalPM templates rebuilt as dynamic-fields per-file YAMLs; shareable via `copalpm template export/import`).

---

## What this repo is

Three independently usable open-source tools that pair for media/VFX production workflows:

| Package | Purpose |
|---------|---------|
| [copalvx](./copalvx/) | Content-addressable version exchange — push/pull large file folders to a central server, versioned. FastAPI + PostgreSQL + SeaweedFS server; Python client (CLI + terminal dashboard). |
| [copalpm](./copalpm/) | Terminal project management + time tracking. File-based (`project.yaml` per project + JSON registry). Optional integration with CopalVX. |
| [copalblender](./copalblender/) | Blender addon installer + plugin. Auto-starts CopalPM time tracking for the project a `.blend` file belongs to. Stops on Blender quit, file close, prolonged unfocus, or cursor inactivity. |

Either can be installed standalone. CopalVX's `pm_hooks` integration with CopalPM is opt-in (activates only when `copalpm` is on PATH). CopalBlender's tracking is a no-op when CopalPM is missing or its daemon is down — Blender keeps working normally.

License: Apache 2.0 — see [LICENSE](./LICENSE), [NOTICE](./NOTICE).

---

## Monorepo layout

```
copal/
├── copalvx/              # CopalVX package
│   ├── CLAUDE.md         # CopalVX-specific architecture, API, gotchas
│   ├── README.md         # User-facing install + usage
│   ├── LICENSE           # Per-package Apache 2.0 (allows standalone redistribution)
│   ├── NOTICE
│   ├── client/           # Python client — installable via uv tool install
│   └── server/           # Docker Compose stack (FastAPI + Postgres + SeaweedFS)
├── copalpm/              # CopalPM package
│   ├── CLAUDE.md         # CopalPM-specific layout, CLI surface, gotchas
│   ├── README.md         # User-facing install + usage
│   ├── LICENSE           # Per-package Apache 2.0
│   ├── NOTICE
│   ├── pyproject.toml
│   ├── src/copalpm/      # Python package (single `copalpm` binary)
│   └── tests/            # pytest suite (see copalpm/CLAUDE.md for current count)
├── copalblender/         # CopalBlender package — Blender addon + installer
│   ├── CLAUDE.md         # CopalBlender-specific architecture, gotchas
│   ├── README.md         # User-facing install + usage
│   ├── LICENSE
│   ├── NOTICE
│   ├── pyproject.toml
│   ├── src/copalblender/ # `copalblender install|uninstall|status` CLI
│   │   └── assets/addon/copal_blender/  # The Blender addon, copied into scripts/addons/
│   └── tests/            # pytest suite (93 unit + 4 integration)
├── LICENSE               # Umbrella Apache 2.0
├── NOTICE
├── README.md             # Public-facing intro to all three packages
├── MERGE_PLAN.md         # Historical record of the rebrand/monorepo migration + post-rebrand phases
└── CLAUDE.md             # You are here
```

History from both source repos is preserved (subtree merge, not squash):
- `git log -- copalvx/` and `git log -- copalpm/` show each package's commits
- The Phase 3.5 history rewrite (filter-repo) sits at the head; old hashes from before the rewrite are gone

---

## Conventions across both packages

### Independence
- **Each package is installable standalone** via `uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copalvx/client"` (or `copalpm` substituted).
- **Cross-package coupling is runtime-only** via subprocess (`copalvx` → `copalpm record ...` via `pm_hooks.py`). No shared Python imports.
- **Non-fatal hook contract:** if one package is missing from PATH, the other warns and continues. Never raise.

### Repo URLs
- `github.com/copal-tools/copal` — the monorepo (canonical)
- `github.com/Sifdone/Copal-VX` and `github.com/Sifdone/ProjectRegistry` — pre-rebrand standalone repos, slated for archive (see MERGE_PLAN.md follow-ups)
- Domains: `copalvx.com` (owned), `copalpm.app` (pending purchase)

### Identity
- All commit history scrubbed to use the GitHub noreply email (`51947061+Sifdone@users.noreply.github.com`) — see Phase 3.5 in [MERGE_PLAN.md](./MERGE_PLAN.md). Future commits should continue using that address (`git config --global user.email ...`).

### Test commands
```powershell
# CopalPM
uv run --directory copalpm pytest

# CopalVX (requires server up for integration tests; unit tests run standalone)
cd copalvx/client && uv run pytest

# CopalBlender
uv run --directory copalblender pytest
```

---

## Working in this repo

Always operate on the monorepo at `E:\Development\copal\`. The two pre-rebrand source dirs (`E:\Development\Copal-VX\` and `E:\Development\ProjectRegistry\`) are now historical; don't edit them — they're archive material.

For working directory-specific config:
- The CopalVX client reads `~/.copal/config.json` per user. Its `client_path` key must now point at `<monorepo>/copalvx/client/` (e.g. `E:\Development\copal\copalvx\client`), not the old standalone path.
- The CopalPM data dir is `%APPDATA%\copalpm\` (Windows) / `~/.config/copalpm/` (Mac/Linux).

---

## Status

| Phase | Status |
|-------|--------|
| Phases 0–4 (rebrand + monorepo + audit + restructure) | ✅ Complete |
| Phase 5 (public launch readiness — CI, real PyPI releases, archive old repos) | ⏳ Pending (legacy migration shim removed 2026-05-15) |
| Phase 6 (feature work: folder picker, pull-dest memory, push/pull activity log, server hardening, OS triggers) | ✅ Complete |
| Phase 7 (auth on CopalVX server) | ⏳ Deferred, LAN-only system |
| Phase 8 (`copalblender` Blender addon — DCC time-tracking integration) | ✅ Complete (2026-05-16) |
| Figma UI redesign for CopalPM | ⏳ User-driven, separate |

**Phase 6 — feature work (2026-05-13).** Four CopalVX/CopalPM features and a
server-deployment overhaul. Shipped:
- **F2 — Folder picker in CopalPM's New Project screen** (textual-fspicker).
- **F3 — Pull destination remembering** — the CopalVX client now skips the
  destination prompt for previously-pulled projects and remembers per-project
  paths in `~/.copal/projects.json`.
- **F1 — Push/pull activity log** — new `events` table on the server, identity
  headers (`X-Copal-User`, `X-Copal-Host`) on every push/pull, "Recent Activity"
  section in CopalVX TUI, "Recent push / Recent pull" rows in CopalPM TUI.
- **Server hardening** — the SeaweedFS filer leveldb is now on a persistent
  mount (lost once on 2026-05-13 during the wrong-repo deploy). New
  [`copalvx/server/DEPLOY.md`](./copalvx/server/DEPLOY.md) runbook covers
  routine / schema / breaking-protocol / clean-slate deploys with the
  storage contract and lessons learned.
- Also bundled: CopalPM TUI perf fix (active-session HTTP polling moved off
  the render thread); user-data dir migration `project-registry/` → `copalpm/`;
  Windows utf-8 stdout fix for copalvx.
- **F4 — OS-level triggers (2026-05-14)** — `copalpm shell-integration
  install` adds three right-click verbs on Windows Explorer (HKCU registry,
  no admin) and macOS Finder (Automator Quick Actions). Verbs: Start Timer,
  Stop Timer, New Project Here. "New Project Here" deep-links into the TUI's
  InitScreen with the folder pre-filled via a new `copalpm tui --screen init
  --dir PATH` flag. Notifications via WinRT toast on Windows, `osascript` on
  macOS. The `time_cli._api()` client was refactored to raise
  `ServiceDownError` / `ApiError` so the verb handlers can surface "service
  not running" as a toast instead of a hard exit. See
  [copalpm/CLAUDE.md](./copalpm/CLAUDE.md) "Shell integration" for details.

**Phase 8 — Blender plugin (2026-05-16).** New sibling package
[`copalblender/`](./copalblender/) ships a Blender addon that auto-starts
a CopalPM time-tracking session for the project a `.blend` belongs to.
Stops on Blender quit, file close (including switching to a file in a
different project or no project), prolonged unfocus (≥5 min default), or
cursor inactivity (2 consecutive ticks with no movement, default). Uses
the hybrid transport: subprocess to `copalpm whose --json` for one-shot
project lookup, direct HTTP to `127.0.0.1:5123` for `/start`/`/stop`/`/ping`/
`/state`. `copalblender install` copies the addon into every detected
Blender version's `scripts/addons/copal_blender/`. 93 unit + 4 integration
tests. See [copalblender/CLAUDE.md](./copalblender/CLAUDE.md) for
architecture and gotchas.

Tracked follow-ups in [MERGE_PLAN.md](./MERGE_PLAN.md):
- Archive old standalone repos

---

## Working with Claude

This repo ships a project-scoped Claude scaffolding (slash commands, subagents, hooks, permission allowlist) in `.claude/`. See [WORKFLOW.md](./WORKFLOW.md) for the full protocol.

Quick references:
- **Slash commands** — `/copal-status`, `/copal-test`, `/copal-deploy`, `/copal-phase-open`, `/copal-phase-close`, `/copal-handoff`, `/copal-gotcha-check`, `/copal-doc-check`. Defined under [.claude/commands/](./.claude/commands/).
- **Subagents** — `copal-gotcha-reviewer`, `copal-doc-curator`, `copal-cross-package`. Defined under [.claude/agents/](./.claude/agents/).
- **Standard work loop** — explore → plan → implement → test (`/copal-test`) → docs (`/copal-doc-check`) → commit → optional handoff (`/copal-handoff`).
- **Cross-package contract** — any change touching `copalvx/client/copal_core/pm_hooks.py` or `copalpm/src/copalpm/copalvx_api.py` must run `/copal-cross-package` (invokes the cross-package subagent) and update both packages' CLAUDE.md in the same commit. Similarly, any change to the daemon HTTP endpoints in `copalpm/src/copalpm/task_tracker.py` must be mirrored in BOTH `copalpm/src/copalpm/time_cli.py` (CopalPM client) AND `copalblender/src/copalblender/assets/addon/copal_blender/copalpm_client.py` (the addon's vendored copy).

### Where does each fact live?

| Fact type | Goes in | Notes |
|---|---|---|
| Code pattern or gotcha intrinsic to one package | `<package>/CLAUDE.md` | Lives next to the code. |
| Cross-cutting convention (test commands, identity, monorepo layout) | This file (umbrella `CLAUDE.md`) | Single source of truth at the top. |
| Project status, phase progress | Status table above + `project_status.md` memory | Memory for cross-session, CLAUDE.md for newcomers. |
| Historical migration narrative | [MERGE_PLAN.md](./MERGE_PLAN.md) | Append-only; deleted post-Phase-5. |
| Future tracked follow-ups | MERGE_PLAN.md (now) → ROADMAP.md (post-Phase-5) | Single open-followups list. |
| Per-feature deep design | Per-package CLAUDE.md or external doc | Keep umbrella scannable. |
| Onboarding for contributors | CONTRIBUTING.md (Phase 5 deliverable) | OSS-facing. |
| Release notes | CHANGELOG.md (Phase 5 deliverable) | OSS-facing. |
| Personal preferences (commit style, env, etc.) | User-global memory (`~/.claude/projects/.../memory/`) | Never checked in. |

Anti-patterns: don't duplicate the same fact across CLAUDE.md and memory; remove CLAUDE.md mentions in the same commit that removes the underlying code; gotchas stay in "Critical Gotchas" until the root cause is gone.

---

## Where to read next

If you're working on:
- **CopalVX (server, client, push/pull, API)** → [copalvx/CLAUDE.md](./copalvx/CLAUDE.md)
- **CopalPM (project registry, time tracking, TUI)** → [copalpm/CLAUDE.md](./copalpm/CLAUDE.md)
- **CopalBlender (Blender addon + installer)** → [copalblender/CLAUDE.md](./copalblender/CLAUDE.md)
- **A new DCC plugin (Maya / Houdini / Nuke / ...)** → "Template for future DCC plugins" in [copalblender/CLAUDE.md](./copalblender/CLAUDE.md). copalblender is the reference implementation.
- **The migration history / lessons learned** → [MERGE_PLAN.md](./MERGE_PLAN.md)
- **The Claude scaffolding (workflow, slash commands, subagents)** → [WORKFLOW.md](./WORKFLOW.md)
