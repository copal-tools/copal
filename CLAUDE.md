# CLAUDE.md — Copal Tools (monorepo umbrella)

> Read this first for orientation across the monorepo.
> Per-package detail lives in [copalvx/CLAUDE.md](./copalvx/CLAUDE.md) and [copalpm/CLAUDE.md](./copalpm/CLAUDE.md).
> Last updated: 2026-05-13 (after Phase 4 monorepo restructure).

---

## What this repo is

Two independently usable open-source tools that pair for media/VFX production workflows:

| Package | Purpose |
|---------|---------|
| [copalvx](./copalvx/) | Content-addressable version exchange — push/pull large file folders to a central server, versioned. FastAPI + PostgreSQL + SeaweedFS server; Python client (CLI + terminal dashboard). |
| [copalpm](./copalpm/) | Terminal project management + time tracking. File-based (`project.yaml` per project + JSON registry). Optional integration with CopalVX. |

Either can be installed standalone. CopalVX's `pm_hooks` integration with CopalPM is opt-in (activates only when `copalpm` is on PATH).

License: Apache 2.0 — see [LICENSE](./LICENSE), [NOTICE](./NOTICE).

---

## Monorepo layout

```
copal/
├── copalvx/          # CopalVX package
│   ├── CLAUDE.md     # CopalVX-specific architecture, API, gotchas
│   ├── README.md     # User-facing install + usage
│   ├── LICENSE       # Per-package Apache 2.0 (allows standalone redistribution)
│   ├── NOTICE
│   ├── client/       # Python client — installable via uv tool install
│   └── server/       # Docker Compose stack (FastAPI + Postgres + SeaweedFS)
├── copalpm/          # CopalPM package
│   ├── CLAUDE.md     # CopalPM-specific layout, CLI surface, gotchas
│   ├── README.md     # User-facing install + usage
│   ├── LICENSE       # Per-package Apache 2.0
│   ├── NOTICE
│   ├── pyproject.toml
│   ├── src/copalpm/  # Python package (single `copalpm` binary)
│   └── tests/        # pytest suite (61 tests)
├── LICENSE           # Umbrella Apache 2.0
├── NOTICE
├── README.md         # Public-facing intro to both packages
├── MERGE_PLAN.md     # Historical record of the rebrand/monorepo migration
└── CLAUDE.md         # You are here
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
```

---

## Working in this repo

Always operate on the monorepo at `E:\Development\copal\`. The two pre-rebrand source dirs (`E:\Development\Copal-VX\` and `E:\Development\ProjectRegistry\`) are now historical; don't edit them — they're archive material.

For working directory-specific config:
- The CopalVX client reads `~/.copal/config.json` per user. Its `client_path` key must now point at `<monorepo>/copalvx/client/` (e.g. `E:\Development\copal\copalvx\client`), not the old standalone path.
- The CopalPM data dir is `%APPDATA%\copalpm\` (Windows) / `~/.config/copalpm/` (Mac/Linux). Auto-migrated from the pre-rebrand `project-registry/` path on first import after upgrade — see CopalPM's gotcha #1.

---

## Status

| Phase | Status |
|-------|--------|
| Phases 0–4 (rebrand + monorepo + audit + restructure) | ✅ Complete |
| Phase 5 (public launch readiness — CI, real PyPI releases, archive old repos) | ⏳ Pending |
| Phase 6 (feature work: folder picker, pull-dest memory, push/pull activity log, server hardening) | ✅ Complete (F4 OS triggers still open) |
| Phase 7 (auth on CopalVX server) | ⏳ Deferred, LAN-only system |
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

Still open under Phase 6: **F4 — OS-level triggers** (Windows Explorer
right-click menus, macOS Quick Actions for start/stop timer + new project).

Tracked follow-ups in [MERGE_PLAN.md](./MERGE_PLAN.md):
- Archive old standalone repos

---

## Where to read next

If you're working on:
- **CopalVX (server, client, push/pull, API)** → [copalvx/CLAUDE.md](./copalvx/CLAUDE.md)
- **CopalPM (project registry, time tracking, TUI)** → [copalpm/CLAUDE.md](./copalpm/CLAUDE.md)
- **The migration history / lessons learned** → [MERGE_PLAN.md](./MERGE_PLAN.md)
