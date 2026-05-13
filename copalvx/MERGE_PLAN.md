# Copal Tools — Rebrand & Monorepo Plan

> Working doc for the CopalVX + ProjectRegistry → `copal-tools/copal` monorepo migration.
> Delete this file once Phase 5 is done.
> Started: 2026-05-13.

---

## Decisions (locked)

| Decision | Value |
|----------|-------|
| Umbrella brand | **Copal Tools** (the family) |
| Flagship product | **CopalVX** — Version eXchange (content-addressable asset storage) |
| Companion product | **CopalPM** — Project management (includes built-in time tracking, no separate `TT` brand) |
| GitHub org | `copal-tools` (✅ created) |
| Monorepo name | `copal` (`github.com/copal-tools/copal`) |
| Top-level layout | `copal/copalvx/` + `copal/copalpm/` + shared `LICENSE` / `NOTICE` / `README.md` |
| License | **Apache 2.0** (both packages) |
| Copyright holder | `The Copal Tools Authors` (generic, contributor-friendly) |
| Domain (VX) | `copalvx.com` (owned) |
| Domain (PM) | `copalpm.app` (preferred — Google-owned TLD, HSTS-preload by default) |
| PyPI names | `copalvx`, `copalpm` (✅ squatted as v0.0.0) |
| Git history | Preserve via `git subtree add --prefix=...` for both repos |
| CLI rename | Do as part of Phase 2: `pm` / `project` / `tt` / `deliver` / `task-tracker` / `pm-tui` → consolidate under `copalpm` |
| VX→PM integration | Stays runtime-decoupled (non-fatal subprocess); becomes opt-in only after user configures a server |

## Decisions still pending

- Marketing landing page (copal.studio? subpath on copalvx.com?) — deferred, not blocking

## Phase 2 design decisions (locked 2026-05-13)

- **CLI shape:** single `copalpm` command with subcommands (e.g. `copalpm tui`, `copalpm time start`). No multiple top-level commands.
- **Backwards-compat aliases:** none. Old `pm` / `tt` / `project` / `deliver` / `task-tracker` / `pm-tui` are removed outright.
- **`pm_hooks.py` rewrites:** `project copalvx-update` → `copalpm copalvx-update`; `pm flush-time` → `copalpm time flush`.
- **Scope:** strict rename + CLI consolidation only. No opportunistic cleanups bundled into Phase 2 — any other changes go in follow-up commits.

---

## Phased plan

Each phase ends with a working state. You can stop after any phase.

### Phase 0 — Squat the names (you do this; ~30 min)

Goal: lock in identity before any code work. Names disappear fast.

1. ✅ Register PyPI placeholders for `copalvx` and `copalpm` (v0.0.0).
2. ✅ Create the GitHub org `copal-tools` and an empty `copal` repo.
3. ⏳ Buy `copalpm.app`.

### Phase 1 — License both existing repos (Claude does this; ~10 min)

Lock in Apache 2.0 in the *existing* repos so the license history is durable independent of when the merge happens.

- Add `LICENSE` (full Apache 2.0 text) to `Copal-VX/` and `ProjectRegistry/` roots.
- Add `NOTICE` (`Copyright 2026 The Copal Tools Authors`) to both.
- Update both `pyproject.toml` files with `license = "Apache-2.0"` SPDX identifier.
- Commit in each repo.

### Phase 2 — Rename ProjectRegistry internals (✅ COMPLETE, 2026-05-13)

Done inside the ProjectRegistry repo (not during the merge). Key outcomes:

- ✅ Python package renamed `src/project_registry/` → `src/copalpm/` (via `git mv` — history preserved)
- ✅ All `from project_registry...` imports updated to `from copalpm...` (8 sites across 5 files)
- ✅ `pyproject.toml` `name`: `project-registry` → `copalpm`; project URLs + classifiers + keywords added
- ✅ CLI entry points consolidated: 6 separate binaries → single `copalpm = "copalpm.cli:main"`
- ✅ Unified argparse dispatcher in `src/copalpm/cli.py` (option B — handler funcs in modules, all argparse in cli.py); per-module `main()` functions removed from `pm`/`project_record`/`time_cli`/`deliver_cli`
- ✅ Subcommand groups: `tui` (default), `project`, `record`, `time`, `service`, `deliver`, hidden `task-tracker`
- ✅ Service install internals updated: macOS plist label `com.copal-tools.copalpm.task-tracker`, plist `ProgramArguments` is now a 2-element array `[copalpm, task-tracker]`; NSSM service name `CopalPMTaskTracker`, install args `[copalpm_bin, "task-tracker"]`
- ✅ Internal `cmd_rollup` subprocess call (`project sync-time`) updated to `copalpm record sync-time`
- ✅ User-facing string updates: `pm install-service` → `copalpm service install`, etc. (7 sites)
- ✅ CopalVX `pm_hooks.py` updated: 5 subprocess sites + header docstring rewritten
- ✅ Smoke tested: `copalpm --help`, `copalpm project list/status`, `copalpm record get` against existing registry (6 projects preserved)
- ⏳ End-to-end CopalVX push integration test pending (requires server up + a real project folder — user verification)

**Important:** user data directory stayed `project-registry/` (preserves existing data). A future migration step will move it to `copalpm/`.

**Breaking changes for any existing install:**
- Pre-rebrand service installs (plist label `com.projectregistry.task-tracker` / NSSM service `TaskTracker`) must be removed before installing the new service. The old `pm uninstall-service` cleans them up cleanly.

### Phase 3 — Monorepo merge (~2 hours)

```
copal/
├── copalvx/        ← git subtree add from Copal-VX
├── copalpm/        ← git subtree add from ProjectRegistry (post-rename)
├── LICENSE
├── NOTICE
└── README.md       ← umbrella readme, one paragraph per package, links to per-package READMEs
```

- `git subtree add --prefix=copalvx <copal-vx-url> main` (preserves history)
- Same for copalpm
- Per-package READMEs stay where they are (SEO surface)
- Umbrella README is short — one paragraph each on VX and PM, install instructions, links

### Phase 4 — Update CLAUDE.md + integration paths (~1 hour)

- Restructure CLAUDE.md: umbrella section, then per-package sections
- `client_path` config key in `~/.copal/config.json` now points at `<repo>/copalvx`
- Memory updates: `project_status` reflects monorepo state

### Phase 5 — Public launch readiness (when ready)

- GitHub Actions CI: one workflow per package (each runs its own pytest)
- Real PyPI releases (bump from 0.0.0)
- Archive (don't delete) old repos with README pointing at new location
- Per-package READMEs over-explain: "CopalVX is git-for-large-files for media studios" / "CopalPM is a terminal project tracker for VFX/motion design work"

---

## Explicitly *out of scope* for this plan

These are real follow-ons but should not block or be bundled with the rebrand:

- **Phase 7 (auth)** — separate, LAN-only system, low priority
- **Figma redesign** — user-driven, separate
- **PM auto-push opt-in change** — small follow-up after Phase 4 lands

## Post-rebrand follow-ups (track separately)

- **Stale Claude worktrees in `E:\Development\Copal-VX\.claude\worktrees\`.** Around 10 worktrees exist; most are 5-25 commits behind main. Triage list and remove dead branches/worktrees via `git worktree remove` + `git branch -D`. Not blocking Phase 3+ but worth a cleanup pass.
- **User data directory migration** `project-registry/` → `copalpm/` with one-time auto-migrate on first run. Deferred from Phase 2 to preserve existing data.
- **Archive old standalone repos** (`Sifdone/Copal-VX`, `Sifdone/ProjectRegistry`) after the monorepo is live — replace their READMEs with a redirect notice. Don't delete (preserves issue/PR history + external links).

## Risk notes

- ~~Phase 2 is the riskiest step~~ ✅ landed. Subprocess audit + integration smoke check passed.
- The subprocess contract between CopalVX and CopalPM is the only cross-package coupling. After Phase 2 all calls go through the single `copalpm` binary; pre-rebrand `pm`/`project`/`tt` invocations from anywhere outside this repo will break.
- Per CLAUDE.md gotcha #7: `client_path` is required and not auto-detectable. Phase 4 must update existing users' `~/.copal/config.json` (or document the breakage).
- Per CLAUDE.md gotcha #18: anyone with a pre-rebrand background service installed must run the old `pm uninstall-service` before installing the new `copalpm service install` — the old service points at a binary that no longer exists.
