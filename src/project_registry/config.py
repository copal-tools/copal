# src/project_registry/config.py
# Shared paths and configuration for all project-registry tools.

import os
import platform
from pathlib import Path


def get_data_dir() -> Path:
    """Return the platform-appropriate user data directory."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:  # macOS / Linux
        base = Path.home() / ".config"
    return base / "project-registry"


# All tools read from this module so paths stay in sync
DATA_DIR     = get_data_dir()
REGISTRY     = DATA_DIR / "registry.json"
SESSIONS_LOG = DATA_DIR / "sessions.jsonl"
SESSION_FILE = DATA_DIR / "current_session.json"
CONFIG_FILE    = DATA_DIR / "config.json"
TEMPLATES_FILE = DATA_DIR / "templates.json"
