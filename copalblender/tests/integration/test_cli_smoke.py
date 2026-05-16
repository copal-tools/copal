"""Integration smoke test: spawn `copalblender` against synthesized Blender dirs.

Uses platform-specific env var injection so the binary sees a tmp-only view of
the Blender user config tree:

* Windows: ``APPDATA`` controls ``%APPDATA%\\Blender Foundation\\Blender\\``.
* macOS / Linux: ``HOME`` controls ``~/.config/blender/`` (and
  ``~/Library/Application Support/Blender/`` on macOS).

Auto-skips if the ``copalblender`` script isn't on PATH inside the venv.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


COPALBLENDER = shutil.which("copalblender")
if COPALBLENDER is None:
    pytest.skip("copalblender binary not on PATH (run `uv sync` first)", allow_module_level=True)


def _fake_blender_env(tmp_path: Path) -> dict[str, str]:
    """Return an env dict that points platform_paths.blender_user_config_root at tmp."""
    env = dict(os.environ)
    if sys.platform == "win32":
        env["APPDATA"] = str(tmp_path)
    else:
        env["HOME"] = str(tmp_path)
    return env


def _make_versions(tmp_path: Path, versions: list[str]) -> list[Path]:
    if sys.platform == "win32":
        root = tmp_path / "Blender Foundation" / "Blender"
    elif sys.platform == "darwin":
        root = tmp_path / "Library" / "Application Support" / "Blender"
    else:
        root = tmp_path / ".config" / "blender"
    out = []
    for v in versions:
        d = root / v
        d.mkdir(parents=True, exist_ok=True)
        out.append(d)
    return out


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [COPALBLENDER] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def test_status_no_installs(tmp_path):
    env = _fake_blender_env(tmp_path)
    result = _run(["status"], env)
    assert result.returncode == 1
    assert "No Blender installs detected" in (result.stdout + result.stderr)


def test_status_lists_detected_versions(tmp_path):
    _make_versions(tmp_path, ["3.6", "4.0", "4.2"])
    env = _fake_blender_env(tmp_path)
    result = _run(["status"], env)
    assert result.returncode == 0
    out = result.stdout
    assert "3.6: not installed" in out
    assert "4.0: not installed" in out
    assert "4.2: not installed" in out


def test_install_then_status_then_uninstall_roundtrip(tmp_path):
    _make_versions(tmp_path, ["4.0"])
    env = _fake_blender_env(tmp_path)

    install = _run(["install"], env)
    assert install.returncode == 0
    assert "ok" in install.stdout.lower()

    status = _run(["status"], env)
    assert "4.0: installed" in status.stdout

    uninstall = _run(["uninstall"], env)
    assert uninstall.returncode == 0
    assert "ok" in uninstall.stdout.lower()

    status_after = _run(["status"], env)
    assert "4.0: not installed" in status_after.stdout


def test_help_lists_three_subcommands(tmp_path):
    """`copalblender --help` should mention all three commands."""
    env = _fake_blender_env(tmp_path)
    result = _run(["--help"], env)
    assert result.returncode == 0
    assert "install" in result.stdout
    assert "uninstall" in result.stdout
    assert "status" in result.stdout
