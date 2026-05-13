"""Tests for the one-time data dir migration from project-registry/ to copalpm/.

Exercises `_resolve_data_dir()` directly with `tmp_path` so the test never
touches the real user data directory.
"""

import pytest

from copalpm.config import (
    _DATA_DIR_NAME,
    _LEGACY_DIR_NAME,
    _MIGRATION_MARKER,
    _resolve_data_dir,
)


def test_fresh_install_returns_new_dir_without_creating_it(tmp_path):
    """No legacy dir, no new dir — returns the new dir path, doesn't mkdir."""
    result = _resolve_data_dir(tmp_path)
    assert result == tmp_path / _DATA_DIR_NAME
    # _resolve_data_dir doesn't mkdir; callers do that as needed
    assert not result.exists()


def test_already_migrated_uses_new_dir(tmp_path):
    """New dir already exists — return it, never touch the legacy dir."""
    new_dir = tmp_path / _DATA_DIR_NAME
    new_dir.mkdir()
    (new_dir / "registry.json").write_text('{"already": "here"}')

    legacy_dir = tmp_path / _LEGACY_DIR_NAME
    legacy_dir.mkdir()
    (legacy_dir / "stale.txt").write_text("don't touch me")

    result = _resolve_data_dir(tmp_path)

    assert result == new_dir
    assert (new_dir / "registry.json").read_text() == '{"already": "here"}'
    # Legacy directory left completely untouched
    assert (legacy_dir / "stale.txt").read_text() == "don't touch me"


def test_migration_copies_legacy_data(tmp_path):
    """Legacy exists, new doesn't — copy legacy into new, leave legacy as backup."""
    legacy_dir = tmp_path / _LEGACY_DIR_NAME
    legacy_dir.mkdir()
    (legacy_dir / "registry.json").write_text('{"projects": []}')
    (legacy_dir / "sessions.jsonl").write_text("line1\nline2\n")

    result = _resolve_data_dir(tmp_path)

    new_dir = tmp_path / _DATA_DIR_NAME
    assert result == new_dir
    assert new_dir.exists()
    # Data copied over
    assert (new_dir / "registry.json").read_text() == '{"projects": []}'
    assert (new_dir / "sessions.jsonl").read_text() == "line1\nline2\n"
    # Marker file written
    marker = new_dir / _MIGRATION_MARKER
    assert marker.exists()
    assert _LEGACY_DIR_NAME in marker.read_text()
    # Legacy preserved as backup
    assert (legacy_dir / "registry.json").read_text() == '{"projects": []}'
    assert (legacy_dir / "sessions.jsonl").read_text() == "line1\nline2\n"


def test_migration_preserves_nested_directories(tmp_path):
    """copytree handles subdirectories too."""
    legacy_dir = tmp_path / _LEGACY_DIR_NAME
    (legacy_dir / "subdir").mkdir(parents=True)
    (legacy_dir / "subdir" / "nested.txt").write_text("nested content")
    (legacy_dir / "subdir" / "deeper").mkdir()
    (legacy_dir / "subdir" / "deeper" / "leaf.txt").write_text("deep")

    _resolve_data_dir(tmp_path)

    new_dir = tmp_path / _DATA_DIR_NAME
    assert (new_dir / "subdir" / "nested.txt").read_text() == "nested content"
    assert (new_dir / "subdir" / "deeper" / "leaf.txt").read_text() == "deep"


def test_migration_is_idempotent(tmp_path):
    """Second call after migration is a no-op — doesn't re-copy or overwrite."""
    legacy_dir = tmp_path / _LEGACY_DIR_NAME
    legacy_dir.mkdir()
    (legacy_dir / "registry.json").write_text('{"original": true}')

    # First call: migrates
    _resolve_data_dir(tmp_path)

    # User modifies the new dir (simulating real use after migration)
    new_dir = tmp_path / _DATA_DIR_NAME
    (new_dir / "registry.json").write_text('{"modified": true}')

    # Second call: must not re-migrate, must not overwrite
    result = _resolve_data_dir(tmp_path)

    assert result == new_dir
    assert (new_dir / "registry.json").read_text() == '{"modified": true}'


def test_migration_writes_marker_with_timestamp(tmp_path):
    """The marker file records the source path and a timestamp."""
    legacy_dir = tmp_path / _LEGACY_DIR_NAME
    legacy_dir.mkdir()
    (legacy_dir / "registry.json").write_text("{}")

    _resolve_data_dir(tmp_path)

    marker_text = (tmp_path / _DATA_DIR_NAME / _MIGRATION_MARKER).read_text()
    assert str(legacy_dir) in marker_text
    # ISO 8601 timestamp present (rough check)
    assert "T" in marker_text  # date/time separator
    assert "+00:00" in marker_text or "Z" in marker_text


def test_migration_falls_back_to_legacy_on_copy_failure(tmp_path, monkeypatch):
    """If copytree fails, fall back to the legacy dir so the tool keeps working."""
    legacy_dir = tmp_path / _LEGACY_DIR_NAME
    legacy_dir.mkdir()
    (legacy_dir / "registry.json").write_text("{}")

    # Sabotage copytree
    import copalpm.config as cfg

    def boom(*a, **kw):
        raise PermissionError("simulated failure")

    monkeypatch.setattr(cfg.shutil, "copytree", boom)

    result = _resolve_data_dir(tmp_path)

    # Fell back to legacy
    assert result == legacy_dir
    # New dir was not partially created
    assert not (tmp_path / _DATA_DIR_NAME).exists()
