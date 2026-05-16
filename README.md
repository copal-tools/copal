# Copal Tools

Open-source tools for media and VFX production pipelines.

| Package | What it does |
|---------|--------------|
| [**copalvx**](./copalvx/) | Content-addressable version exchange. Push a folder of files to a central server, pull any version on any machine on the LAN. "Git for large files," purpose-built for studio asset workflows. Postgres + SeaweedFS server, Python client, terminal dashboard. |
| [**copalpm**](./copalpm/) | Terminal project management and time tracking for motion design and VFX work. File-based (`project.yaml` per project, JSON registry, append-only session log). Optional integration with CopalVX so versions and time data travel together. |
| [**copalblender**](./copalblender/) | Blender addon that auto-starts CopalPM time tracking for the project a `.blend` file belongs to. Stops on Blender quit, file close, prolonged unfocus, or cursor inactivity. Installs into every detected Blender version with one command. |

Each package is **independently installable and usable**. Run either one on its own; pair them for the full workflow.

---

## Project status

Early-but-functional. Used daily by the original author across Windows + macOS + Linux server. Phase 7 (auth) and a UI redesign are the remaining major items; everything else is stable.

See each package's README for current command surface and install instructions.

---

## Install

Both packages install as `uv` tools — no Python wrangling required.

```bash
# CopalVX
uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copalvx/client"

# CopalPM
uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copalpm"

# CopalBlender (Blender addon)
uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copalblender"
copalblender install      # copies the addon into every detected Blender version
```

(The CopalVX server runs separately via Docker Compose; see [copalvx/server/](./copalvx/server/).)

---

## Layout

```
copal/
├── copalvx/              # CopalVX — version exchange
│   ├── client/           # Python CLI + TUI (`copalvx push/pull`)
│   ├── server/           # FastAPI + PostgreSQL + SeaweedFS (Docker Compose)
│   ├── README.md         # CopalVX-specific docs
│   ├── LICENSE
│   └── NOTICE
├── copalpm/              # CopalPM — project + time tracking
│   ├── src/copalpm/      # Python package (unified CLI: `copalpm`)
│   ├── tests/            # pytest suite
│   ├── README.md         # CopalPM-specific docs
│   ├── LICENSE
│   └── NOTICE
├── copalblender/         # CopalBlender — Blender addon + installer
│   ├── src/copalblender/ # `copalblender install|uninstall|status` CLI + bundled addon
│   ├── tests/            # pytest suite
│   ├── README.md
│   ├── LICENSE
│   └── NOTICE
├── LICENSE               # Apache 2.0 (this file applies to the whole project)
├── NOTICE                # Attribution
└── README.md             # You are here
```

History from the two original standalone repos is preserved: `git log -- copalvx/` and `git log -- copalpm/` show each package's full commit history.

---

## License

Apache 2.0 — see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
