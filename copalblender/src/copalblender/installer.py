# src/copalblender/installer.py
# Blender-install detection + addon copy/remove/probe.

from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path

from copalblender import platform_paths


ADDON_DIRNAME = "copal_blender"


def _addon_source_dir() -> Path:
    """Return the on-disk path of the bundled addon source.

    Resolved via `importlib.resources.files` so it works whether the package
    is installed editable, installed normally, or run from a wheel.
    """
    return Path(str(files("copalblender").joinpath("assets/addon/copal_blender")))


def _addon_dest_for(version_dir: Path) -> Path:
    """Return the install destination under a Blender version dir."""
    return version_dir / "scripts" / "addons" / ADDON_DIRNAME


def detect_installs() -> list[Path]:
    """Return version dirs (e.g. .../Blender/4.2/) detected on this OS."""
    return platform_paths.list_blender_versions(platform_paths.blender_user_config_root())


def install_addon(versions: list[Path]) -> list[tuple[str, bool, str]]:
    """Copy the addon into each version's scripts/addons/copal_blender/.

    Returns one tuple per version: (version_name, success, message).
    `dirs_exist_ok=True` makes this idempotent — re-running overwrites in place.
    """
    src = _addon_source_dir()
    if not src.exists():
        # Packaging error — wheel didn't include the addon source.
        return [(v.name, False, f"addon source missing at {src}") for v in versions]

    out: list[tuple[str, bool, str]] = []
    for v in versions:
        dst = _addon_dest_for(v)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
            out.append((v.name, True, f"installed to {dst}"))
        except OSError as e:
            out.append((v.name, False, f"{type(e).__name__}: {e}"))
    return out


def uninstall_addon(versions: list[Path]) -> list[tuple[str, bool, str]]:
    """Remove the addon directory from each version's scripts/addons/.

    Returns one tuple per version: (version_name, success, message).
    A missing addon directory is treated as success ("nothing to remove").
    """
    out: list[tuple[str, bool, str]] = []
    for v in versions:
        dst = _addon_dest_for(v)
        if not dst.exists():
            out.append((v.name, True, "nothing to remove"))
            continue
        try:
            shutil.rmtree(str(dst))
            out.append((v.name, True, f"removed from {dst}"))
        except OSError as e:
            out.append((v.name, False, f"{type(e).__name__}: {e}"))
    return out


def status(versions: list[Path]) -> list[tuple[str, bool, Path]]:
    """For each version: (name, addon_directory_exists, expected_path)."""
    return [(v.name, _addon_dest_for(v).exists(), _addon_dest_for(v)) for v in versions]
