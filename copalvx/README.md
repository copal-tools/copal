# Copal-VX (Version Exchange)

**Copal-VX** is a content-addressable asset management system for media/VFX pipelines. Push a folder of files to a central server (versioned), pull any version back on any machine on the LAN. Think "git for large files" — without the complexity.

Files are stored by SHA-256 hash. Uploading the same bytes twice is a no-op. A "version" is just a pointer: project name + version tag = a list of hashes.

## Architecture

| Layer | Component | Port |
|-------|-----------|------|
| Object storage | SeaweedFS Filer | 8888 |
| API | FastAPI | 8005 |
| Database | PostgreSQL 15 | internal |
| Client | Python CLI / TUI | — |

The server runs in Docker on a Linux machine. The client runs on Windows and macOS.

---

## Client Install

### Mac

```bash
# 1. Install uv (skip if already installed)
brew install uv

# 2. Add uv tools to PATH (one-time shell config)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# 3. Install CopalVX
uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copalvx/client"

# 4. Configure (server IP, author name, projects root)
copalvx setup
```

### Windows

```powershell
# 1. Install uv (skip if already installed)
winget install astral-sh.uv

# 2. Install CopalVX
uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copalvx/client"

# 3. Configure
copalvx setup
```

### Update

```bash
uv tool upgrade copalvx
```

---

## Server Setup

The server runs on a dedicated Linux machine via Docker Compose.

```bash
# Clone the repo on the server
git clone https://github.com/copal-tools/copal.git
cd copal/copalvx/server

# Create .env from the template and fill in your values
cp .env.example .env

# Start all services (API + PostgreSQL + SeaweedFS)
docker-compose up -d
```

To update the API after a code change (DB and SeaweedFS keep running):
```bash
git pull
docker-compose up -d --build asset-api
```

---

## Usage

```bash
copalvx           # Open the dashboard
copalvx setup     # Reconfigure (server IP, author, projects root)
```

The dashboard shows system health (API / DB / SeaweedFS), lists all projects, and lets you push, pull, or delete from the terminal.

---

## Integration with ProjectRegistry

Push and pull can also be triggered from the CopalPM TUI using the `p` and `l` keybindings in the project detail screen. See [copalpm/](../copalpm/) in this monorepo for setup.

---

## Default Config

Stored at `~/.copal/config.json` after running `copalvx setup`.

| Key | Default | Description |
|-----|---------|-------------|
| `server_ip` | `192.168.1.100` | Server address (set during `copalvx setup`) |
| `api_port` | `8005` | FastAPI port |
| `filer_port` | `8888` | SeaweedFS filer port |
| `default_author` | system username | Name used in commits |
| `default_projects_root` | `~/Projects` | Default directory for push/pull |
