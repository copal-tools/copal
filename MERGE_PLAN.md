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

- Whether to keep short CLI aliases (`pm`, `tt`) alongside the new `copalpm` command — deferred to Phase 2
- Marketing landing page (copal.studio? subpath on copalvx.com?) — deferred, not blocking

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

### Phase 2 — Rename ProjectRegistry internals (Claude + you; ~half a day)

Do this *inside the existing ProjectRegistry repo*, not during the merge. One concern at a time.

- Python package: `project_registry/` → `copalpm/`
- All imports updated (`from project_registry...` → `from copalpm...`)
- `pyproject.toml` `name`: `project-registry` → `copalpm`
- CLI entry points: consolidate `pm`, `project`, `tt`, `deliver`, `task-tracker`, `pm-tui` under a single `copalpm` command with subcommands (final subcommand layout TBD — sketch in Phase 2 design note).
- Update CopalVX side: `pm_hooks.py` references to `project copalvx-update` and `pm flush-time` → `copalpm <subcommand>`.
- Run both test suites; fix breakage.
- Verify end-to-end (CopalVX push triggers PM hooks correctly).
- Commit. CopalVX continues to work.

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

## Risk notes

- Phase 2 is the riskiest step — renaming a package + consolidating CLIs touches every subprocess call site. Do it in its own commit series, not bundled with the monorepo merge.
- The subprocess contract between CopalVX and CopalPM is the only cross-package coupling. Audit every `project` / `pm` / `tt` call before Phase 2 commits land.
- Per CLAUDE.md gotcha #7: `client_path` is required and not auto-detectable. Phase 4 must update existing users' `~/.copal/config.json` (or document the breakage).
