"""Unit tests for copalblender.installer.detect_installs."""

from pathlib import Path

from copalblender import installer, platform_paths


def test_detect_installs_empty_root(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths, "blender_user_config_root", lambda: tmp_path / "missing")
    assert installer.detect_installs() == []


def test_detect_installs_finds_versions(tmp_path, monkeypatch):
    (tmp_path / "3.6").mkdir()
    (tmp_path / "4.0").mkdir()
    (tmp_path / "4.2").mkdir()
    (tmp_path / "scripts").mkdir()  # not a version
    monkeypatch.setattr(platform_paths, "blender_user_config_root", lambda: tmp_path)
    names = [p.name for p in installer.detect_installs()]
    assert names == ["3.6", "4.0", "4.2"]


def test_detect_installs_natural_sort_order(tmp_path, monkeypatch):
    for v in ["4.10", "4.2", "3.6"]:
        (tmp_path / v).mkdir()
    monkeypatch.setattr(platform_paths, "blender_user_config_root", lambda: tmp_path)
    names = [p.name for p in installer.detect_installs()]
    assert names == ["3.6", "4.2", "4.10"]
