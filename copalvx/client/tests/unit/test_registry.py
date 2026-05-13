"""
Unit tests for copal_core.registry helpers.

Uses a monkeypatched REGISTRY_FILE so the user's real ~/.copal/projects.json
is never touched.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from copal_core import registry


def _write_registry(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


def test_lookup_path_missing_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "projects.json")
    assert registry.lookup_path("anything") is None


def test_lookup_path_project_not_in_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "projects.json")
    _write_registry(
        registry.REGISTRY_FILE,
        [{"name": "other", "path": "/x/other", "last_accessed": 1.0}],
    )
    assert registry.lookup_path("missing") is None


def test_lookup_path_returns_known_project(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "projects.json")
    _write_registry(
        registry.REGISTRY_FILE,
        [{"name": "alpha", "path": "/work/alpha", "last_accessed": 100.0}],
    )
    assert registry.lookup_path("alpha") == "/work/alpha"


def test_lookup_path_prefers_most_recent_when_same_name(tmp_path, monkeypatch):
    """If the same project name was pulled to two locations, prefer the
    most recently accessed one. load_registry sorts by last_accessed desc."""
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "projects.json")
    _write_registry(
        registry.REGISTRY_FILE,
        [
            {"name": "shared", "path": "/old/shared", "last_accessed": 100.0},
            {"name": "shared", "path": "/new/shared", "last_accessed": 200.0},
        ],
    )
    assert registry.lookup_path("shared") == "/new/shared"


def test_register_project_then_lookup_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "projects.json")
    registry.register_project("beta", "/work/beta", version="v1.0")
    assert registry.lookup_path("beta") == "/work/beta"
