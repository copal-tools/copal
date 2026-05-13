# src/copalpm/config.py
# Shared paths and configuration for all copalpm tools.
#
# The user data directory was renamed from "project-registry/" to "copalpm/"
# in a follow-up to the Phase 2 rebrand. On first import after upgrade, any
# pre-existing legacy directory is auto-copied to the new location and left
# in place as a backup. See _resolve_data_dir() below.

import os
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_LEGACY_DIR_NAME = "project-registry"
_DATA_DIR_NAME = "copalpm"
_MIGRATION_MARKER = ".migrated_from_project-registry"


def _resolve_data_dir(base: Path) -> Path:
    """Resolve the data directory under `base`, migrating legacy data if needed.

    Returns the path the tool should use. If `<base>/copalpm/` already exists,
    it's used as-is. Otherwise, if `<base>/project-registry/` exists, its
    contents are copied to the new path (one-time migration) and the legacy
    directory is preserved as a backup. On a fresh install (neither exists),
    the new path is returned without being created — callers `mkdir(parents=True,
    exist_ok=True)` as needed.

    If migration fails for any reason, falls back to the legacy directory so
    the tool stays functional. The next run will retry the migration.
    """
    new_dir = base / _DATA_DIR_NAME
    legacy_dir = base / _LEGACY_DIR_NAME

    if new_dir.exists():
        return new_dir
    if not legacy_dir.exists():
        return new_dir  # fresh install — nothing to migrate

    # Legacy data present, new dir missing — perform one-time migration.
    try:
        shutil.copytree(legacy_dir, new_dir)
        (new_dir / _MIGRATION_MARKER).write_text(
            f"Migrated from {legacy_dir}\n"
            f"on {datetime.now(timezone.utc).isoformat()}\n"
            f"The original directory is preserved as a backup. Delete it\n"
            f"manually once you have verified everything works correctly.\n",
            encoding="utf-8",
        )
        print(
            f"[copalpm] Migrated user data: {legacy_dir} -> {new_dir}\n"
            f"[copalpm] Old directory preserved as backup.\n"
            f"[copalpm] If the task-tracker service is currently running, "
            f"restart it so it picks up the new path:\n"
            f"[copalpm]   copalpm service uninstall && copalpm service install",
            file=sys.stderr,
        )
        return new_dir
    except FileExistsError:
        # Another process won the race during a concurrent first run — fine.
        return new_dir
    except Exception as e:
        # Migration failed (permission denied, disk full, etc.). Fall back to
        # the legacy directory so the tool keeps working. The user sees a
        # warning; the next run will retry.
        print(
            f"[copalpm] WARNING: data dir migration failed: {e!r}\n"
            f"[copalpm] Falling back to legacy directory: {legacy_dir}",
            file=sys.stderr,
        )
        return legacy_dir


def get_data_dir() -> Path:
    """Return the platform-appropriate user data directory.

    Auto-migrates from the pre-rebrand `project-registry/` directory on
    first run after upgrade. See `_resolve_data_dir()` for details.
    """
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:  # macOS / Linux
        base = Path.home() / ".config"
    return _resolve_data_dir(base)


# All tools read from this module so paths stay in sync
DATA_DIR       = get_data_dir()
REGISTRY       = DATA_DIR / "registry.json"
SESSIONS_LOG   = DATA_DIR / "sessions.jsonl"
SESSION_FILE   = DATA_DIR / "current_session.json"
CONFIG_FILE    = DATA_DIR / "config.json"
TEMPLATES_FILE = DATA_DIR / "templates.json"
