"""Unit tests for copalblender.installer install/uninstall/status round-trips."""

from pathlib import Path

import pytest

from copalblender import installer


def _make_version_dirs(root: Path, versions: list[str]) -> list[Path]:
    out = []
    for v in versions:
        d = root / v
        d.mkdir(parents=True, exist_ok=True)
        out.append(d)
    return out


def test_install_creates_addon_in_each_version(tmp_path):
    versions = _make_version_dirs(tmp_path, ["3.6", "4.0", "4.2"])
    results = installer.install_addon(versions)
    assert all(ok for _, ok, _ in results)
    for v in versions:
        addon = v / "scripts" / "addons" / "copal_blender" / "__init__.py"
        assert addon.exists(), f"expected addon in {v}"
        # bl_info string is the most-stable marker
        assert "bl_info" in addon.read_text(encoding="utf-8")


def test_install_idempotent(tmp_path):
    versions = _make_version_dirs(tmp_path, ["4.0"])
    installer.install_addon(versions)
    # Re-run shouldn't error.
    results = installer.install_addon(versions)
    assert all(ok for _, ok, _ in results)


def test_install_overwrites_existing(tmp_path):
    """A stale __init__.py from a previous install should be replaced."""
    v = _make_version_dirs(tmp_path, ["4.0"])[0]
    target = v / "scripts" / "addons" / "copal_blender"
    target.mkdir(parents=True)
    (target / "__init__.py").write_text("stale = True\n", encoding="utf-8")
    installer.install_addon([v])
    text = (target / "__init__.py").read_text(encoding="utf-8")
    assert "bl_info" in text
    assert "stale = True" not in text


def test_uninstall_removes_addon(tmp_path):
    versions = _make_version_dirs(tmp_path, ["3.6", "4.0"])
    installer.install_addon(versions)
    results = installer.uninstall_addon(versions)
    assert all(ok for _, ok, _ in results)
    for v in versions:
        assert not (v / "scripts" / "addons" / "copal_blender").exists()


def test_uninstall_missing_is_success(tmp_path):
    """Removing an addon that was never installed should report success."""
    versions = _make_version_dirs(tmp_path, ["4.0"])
    results = installer.uninstall_addon(versions)
    assert results == [("4.0", True, "nothing to remove")]


def test_status_reports_install_state(tmp_path):
    v36, v40 = _make_version_dirs(tmp_path, ["3.6", "4.0"])
    installer.install_addon([v40])  # only 4.0 has the addon
    rows = installer.status([v36, v40])
    by_name = {ver: present for ver, present, _ in rows}
    assert by_name == {"3.6": False, "4.0": True}


def test_install_failure_does_not_stop_other_versions(tmp_path, monkeypatch):
    """If one copy fails, the next version still gets installed."""
    versions = _make_version_dirs(tmp_path, ["3.6", "4.0"])
    real_copytree = installer.shutil.copytree
    calls = {"n": 0}

    def flaky_copytree(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated failure")
        return real_copytree(*args, **kwargs)

    monkeypatch.setattr(installer.shutil, "copytree", flaky_copytree)
    results = installer.install_addon(versions)
    assert results[0][1] is False
    assert results[0][2].startswith("OSError")
    assert results[1][1] is True
