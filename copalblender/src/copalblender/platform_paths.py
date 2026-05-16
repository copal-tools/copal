# src/copalblender/platform_paths.py
# Per-OS path resolution: CopalPM config dir + Blender user-config root.
# Pure functions; takes no arguments other than the OS-detection it does itself.

import os
import platform
import re
from pathlib import Path


_VERSION_RE = re.compile(r"^\d+\.\d+$")


def copalpm_config_path() -> Path:
    """Return the path to CopalPM's config.json on this OS.

    Mirrors copalpm/src/copalpm/config.py:11-17 exactly. Don't drift from it.
    """
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    else:  # macOS / Linux
        base = Path.home() / ".config"
    return base / "copalpm" / "config.json"


def blender_user_config_root() -> Path:
    """Return the root directory under which Blender stores its per-version user config."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "Blender Foundation" / "Blender"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Blender"
    # Linux / other Unix
    return Path.home() / ".config" / "blender"


def list_blender_versions(root: Path) -> list[Path]:
    """Return version directories under `root` matching `<major>.<minor>`, sorted.

    Returns an empty list if `root` doesn't exist or contains no version dirs.
    Sort order is the natural string sort — fine for two-digit majors and minors.
    """
    if not root.exists() or not root.is_dir():
        return []
    versions = [p for p in root.iterdir() if p.is_dir() and _VERSION_RE.match(p.name)]
    versions.sort(key=lambda p: tuple(int(x) for x in p.name.split(".")))
    return versions
