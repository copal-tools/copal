# Copal Tools — Rebrand & Monorepo Plan

> Historical record of the CopalVX + ProjectRegistry → `copal-tools/copal` monorepo migration.
> Phases 0–4 complete (as of 2026-05-13). Only Phase 5 (public launch readiness) + tracked follow-ups remain.
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

### Phase 1 — License both existing repos (✅ COMPLETE, 2026-05-13)

Locked in Apache 2.0 in the *existing* repos so the license history is durable independent of when the merge happened.

- ✅ Added `LICENSE` (full Apache 2.0 text) to `Copal-VX/` and `ProjectRegistry/` roots.
- ✅ Added `NOTICE` (`Copyright 2026 The Copal Tools Authors`) to both.
- ✅ Updated both `pyproject.toml` files with `license = "Apache-2.0"` SPDX identifier.
- ✅ Committed in each repo (`abb5bb9` in Copal-VX worktree, `c0936f5` in ProjectRegistry main).

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

### Phase 3 — Monorepo merge (✅ COMPLETE, 2026-05-13)

```
copal/
├── copalvx/        ← git subtree add from Copal-VX
├── copalpm/        ← git subtree add from ProjectRegistry (post-rename)
├── LICENSE
├── NOTICE
├── MERGE_PLAN.md   ← this file
└── README.md       ← umbrella readme, one paragraph per package, links to per-package READMEs
```

- ✅ FF-merged worktree branch `claude/jolly-pascal-9932bf` (3 commits) into Copal-VX main
- ✅ Pushed Copal-VX main + ProjectRegistry main to origin as backups
- ✅ Initialized monorepo locally at `E:\Development\copal\` with umbrella `LICENSE` / `NOTICE` / `README.md`
- ✅ `git subtree add --prefix=copalvx file:///E:/Development/Copal-VX main` (preserves history)
- ✅ `git subtree add --prefix=copalpm file:///E:/Development/ProjectRegistry main` (preserves history)
- ✅ Per-package READMEs kept in place as SEO surface
- ✅ Origin remote wired to `https://github.com/copal-tools/copal.git`
- ✅ Initial public push completed

### Phase 3.5 — Personal data audit + history rewrite (✅ COMPLETE, 2026-05-13)

After the initial public push, audited for personal/sensitive data. Findings + remediation:

- 🔴 **DB password `secure_password_123`**: was a real (testing) password baked as a default in `server/app/init_db.py` and `database.py`. Forward-fixed to `CHANGE_ME_IN_DOT_ENV`, scrubbed from history via `git filter-repo --replace-text`, force-pushed. **Password rotated on the actual server** in a separate independent action (ALTER USER + `.env` update + API container restart).
- 🟡 **Author home LAN IP `192.168.178.161`** baked as default across 9 files: forward-fixed to `192.168.1.100` (RFC1918 example range). History intentionally not scrubbed (RFC1918 IP, low exposure value).
- 🟡 **Personal names (BBDO/Claudia/Stelios)** as default template values in `pm.py` + `tui_app.py` placeholders: forward-fixed to empty strings / generic placeholders; **scrubbed from history** in the same filter-repo pass.
- 🟡 **Author email `tsiros123@gmail.com`** in every commit's metadata: scrubbed via `git filter-repo --mailmap` to `51947061+Sifdone@users.noreply.github.com`. User also updated global git config so future commits use the noreply form.
- 🟢 **Stale `Sifdone/...` URLs** in `copalvx/README.md`: forward-fixed to the new monorepo URL.
- 🟢 **TLC employer name** as UI label: forward-fixed to "Internal" (kept the underlying schema value `"tlc"` for backwards compat with existing project.yaml files).

Result: 79 commits rewritten. Force-pushed to `copal-tools/copal`. Old `Sifdone/Copal-VX` and `Sifdone/ProjectRegistry` repos still hold unredacted history — slated for archive in Phase 5.

### Phase 4 — Update CLAUDE.md + integration paths (✅ COMPLETE, 2026-05-13)

- ✅ Restructured CLAUDE.md: small umbrella at monorepo root + detailed per-package docs (`copalvx/CLAUDE.md`, `copalpm/CLAUDE.md`)
- ✅ PM-specific gotchas (#17 rename + #18 daemon spec change) moved from `copalvx/CLAUDE.md` to `copalpm/CLAUDE.md`
- ✅ MERGE_PLAN.md relocated from `copalvx/MERGE_PLAN.md` to monorepo root (this file)
- ✅ `client_path` config key documented: now points at `<monorepo>/copalvx/client/` instead of the standalone `Copal-VX/client/` path
- ✅ Memory updates: `project_status` reflects monorepo-complete state

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
