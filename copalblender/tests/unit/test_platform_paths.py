"""Unit tests for copalblender.platform_paths."""

from pathlib import Path

import pytest

from copalblender import platform_paths


# ── copalpm_config_path ────────────────────────────────────────────────────────

def test_copalpm_config_path_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert platform_paths.copalpm_config_path() == tmp_path / "copalpm" / "config.json"


def test_copalpm_config_path_windows_no_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.platform, "system", lambda: "Windows")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(platform_paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert platform_paths.copalpm_config_path() == tmp_path / "copalpm" / "config.json"


def test_copalpm_config_path_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform_paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert platform_paths.copalpm_config_path() == tmp_path / ".config" / "copalpm" / "config.json"


def test_copalpm_config_path_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform_paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert platform_paths.copalpm_config_path() == tmp_path / ".config" / "copalpm" / "config.json"


# ── blender_user_config_root ───────────────────────────────────────────────────

def test_blender_root_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert platform_paths.blender_user_config_root() == tmp_path / "Blender Foundation" / "Blender"


def test_blender_root_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform_paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert platform_paths.blender_user_config_root() == tmp_path / "Library" / "Application Support" / "Blender"


def test_blender_root_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform_paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert platform_paths.blender_user_config_root() == tmp_path / ".config" / "blender"


# ── list_blender_versions ──────────────────────────────────────────────────────

def test_list_versions_missing_root(tmp_path):
    assert platform_paths.list_blender_versions(tmp_path / "does-not-exist") == []


def test_list_versions_empty_root(tmp_path):
    assert platform_paths.list_blender_versions(tmp_path) == []


def test_list_versions_filters_non_version_dirs(tmp_path):
    (tmp_path / "4.0").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "scratch.tmp").touch()  # file, not dir
    (tmp_path / "3.6").mkdir()
    names = [p.name for p in platform_paths.list_blender_versions(tmp_path)]
    assert names == ["3.6", "4.0"]


def test_list_versions_natural_sort(tmp_path):
    """Version 4.10 sorts after 4.2 by numeric comparison, not lexicographic."""
    for v in ["4.2", "4.10", "3.6", "4.0"]:
        (tmp_path / v).mkdir()
    names = [p.name for p in platform_paths.list_blender_versions(tmp_path)]
    assert names == ["3.6", "4.0", "4.2", "4.10"]


def test_list_versions_rejects_three_part(tmp_path):
    """Only `major.minor` is a Blender version dir — patch numbers don't appear here."""
    (tmp_path / "4.0").mkdir()
    (tmp_path / "4.0.1").mkdir()
    names = [p.name for p in platform_paths.list_blender_versions(tmp_path)]
    assert names == ["4.0"]
