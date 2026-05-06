# ProjectRegistry

A lightweight, file-based project management and time-tracking system for media/VFX pipelines. No database, no cloud — plain files, a CLI, and a local HTTP service for session tracking.

## Tools

| Command | Description |
|---------|-------------|
| `pm` | Project manager — create, list, register projects |
| `project` | Per-project record — read/write project.yaml metadata |
| `tt` | Time CLI — start, stop, and query tracking sessions |
| `task-tracker` | Background HTTP service — tracks active sessions on `localhost:5123` |
| `deliver` | Delivery helper |

---

## Install

### Mac

```bash
# 1. Install uv (skip if already installed)
brew install uv

# 2. Add uv tools to PATH (one-time shell config)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# 3. Install ProjectRegistry
uv tool install "git+https://github.com/Sifdone/ProjectRegistry.git"

# 4. Install and start the background time-tracking service
pm install-service
```

### Windows

```powershell
# 1. Install uv (skip if already installed)
winget install astral-sh.uv

# 2. Install ProjectRegistry
uv tool install "git+https://github.com/Sifdone/ProjectRegistry.git"

# 3. Install NSSM (if not already installed)
winget install NSSM.NSSM

# 4. Install and start the background time-tracking service
pm install-service
```

### Update

```bash
uv tool upgrade project-registry
```

---

## Background Service

The `task-tracker` service runs on `localhost:5123` and tracks time sessions. It starts automatically on login after `pm install-service`.

```bash
pm install-service    # Install and start (Mac: launchd, Windows: NSSM)
pm uninstall-service  # Stop and remove
pm service-status     # Check if the service is running
```

---

## Common Commands

```bash
# Projects
pm init "Project Name" --dir /path/to/projects   # Create a new project
pm list                                            # List all registered projects
pm register /path/to/existing/project             # Register an existing folder

# Time tracking
tt start <project-id>    # Start a session
tt stop                  # Stop current session
tt status                # Show active session
pm rollup                # Total hours per project
```

---

## Integration with CopalVX

Push and pull CopalVX versions directly from the project detail screen in `pm` (the TUI) using the `p` and `l` keybindings. See the [CopalVX repo](https://github.com/Sifdone/Copal-VX) for CopalVX setup.

CopalVX must be installed and `copalvx setup` must have been run for the integration to work.
