"""Tests for `_cmd_mark_deliverable` — the shell-trigger handler that
implements the right-click "Copal: Mark as Deliverable" verb.

The handler:
  1. Resolves the file's owning project via `find_project_for_path`.
  2. Either appends to an in-flight deliverable (batch marker fresh + same
     project) or creates a new entry.
  3. Writes project.yaml atomically and refreshes the batch marker.

These tests stub `find_project_for_path` to return a fake match pointing
at a real `project.yaml` in tmp_path.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from copalpm import shell_integration as si
from copalpm.project_lookup import ProjectMatch


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A throwaway Copal project with a usable project.yaml + tmp data dir."""
    proj_root = tmp_path / "proj"
    proj_root.mkdir()
    yaml_path = proj_root / "project.yaml"
    yaml.safe_dump(
        {
            "id":   "PROJ-TEST-010125",
            "name": "Test Project",
            "schema_version": 1,
            "deliverables": [],
        },
        yaml_path.open("w", encoding="utf-8"),
    )

    fake_data = tmp_path / "data"
    fake_data.mkdir()
    monkeypatch.setattr(
        si, "_batch_marker_path",
        lambda: fake_data / si._BATCH_MARKER_NAME,
    )

    match = ProjectMatch(
        project_id="PROJ-TEST-010125",
        project_name="Test Project",
        project_root=proj_root,
        drift=False,
        matched_via="registry",
    )
    monkeypatch.setattr(
        "copalpm.project_lookup.find_project_for_path",
        lambda target, registry=None: match if str(target).startswith(str(proj_root)) else None,
    )

    # Silence toasts during the test
    monkeypatch.setattr(si, "_notify", lambda *a, **kw: None)

    return SimpleNamespace(
        root=proj_root,
        yaml_path=yaml_path,
        match=match,
    )


def _read(yaml_path: Path) -> dict:
    return yaml.safe_load(yaml_path.read_text(encoding="utf-8"))


# ── Create a fresh entry ──────────────────────────────────────────────────────

def test_first_invocation_creates_new_entry(project):
    file_path = project.root / "Final.mp4"
    file_path.write_bytes(b"x")

    rc = si._cmd_mark_deliverable(SimpleNamespace(file=str(file_path)))
    assert rc == 0

    record = _read(project.yaml_path)
    assert len(record["deliverables"]) == 1
    entry = record["deliverables"][0]
    assert entry["paths"] == ["Final.mp4"]
    assert entry["name"] == "Final"
    assert entry["type"] == "draft"
    assert entry["recipient"] == "internal"


def test_marker_written_after_create(project):
    file_path = project.root / "a.mp4"
    file_path.write_bytes(b"x")
    si._cmd_mark_deliverable(SimpleNamespace(file=str(file_path)))

    marker = si._read_batch_marker()
    assert marker is not None
    assert marker["project_id"] == "PROJ-TEST-010125"
    assert marker["deliverable_index"] == 0


# ── Append within batch window ────────────────────────────────────────────────

def test_second_invocation_appends_to_existing_entry(project):
    f1 = project.root / "hero.mp4"
    f2 = project.root / "proxy.mp4"
    f1.write_bytes(b"x"); f2.write_bytes(b"y")

    si._cmd_mark_deliverable(SimpleNamespace(file=str(f1)))
    si._cmd_mark_deliverable(SimpleNamespace(file=str(f2)))

    record = _read(project.yaml_path)
    assert len(record["deliverables"]) == 1, "expected the second call to append, not create"
    assert record["deliverables"][0]["paths"] == ["hero.mp4", "proxy.mp4"]


# ── Cross-project guard ───────────────────────────────────────────────────────

def test_cross_project_invocation_creates_new_entry(project, tmp_path, monkeypatch):
    """If a 2nd mark fires while marker is fresh BUT for a different project,
    the marker must NOT cause the 2nd file to append into the first project's
    entry."""
    f1 = project.root / "a.mp4"; f1.write_bytes(b"x")
    si._cmd_mark_deliverable(SimpleNamespace(file=str(f1)))

    # Build a second project elsewhere
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_yaml = other_root / "project.yaml"
    yaml.safe_dump(
        {"id": "PROJ-OTHER-010125", "name": "Other", "schema_version": 1, "deliverables": []},
        other_yaml.open("w", encoding="utf-8"),
    )
    other_match = ProjectMatch(
        project_id="PROJ-OTHER-010125",
        project_name="Other",
        project_root=other_root,
        drift=False,
        matched_via="registry",
    )
    # Re-route find_project_for_path to map files by which root they live under
    def fake_lookup(target, registry=None):
        target = Path(target)
        if str(target).startswith(str(other_root)):
            return other_match
        if str(target).startswith(str(project.root)):
            return project.match
        return None
    monkeypatch.setattr("copalpm.project_lookup.find_project_for_path", fake_lookup)

    f2 = other_root / "b.mp4"; f2.write_bytes(b"y")
    si._cmd_mark_deliverable(SimpleNamespace(file=str(f2)))

    rec_a = _read(project.yaml_path)
    rec_b = _read(other_yaml)
    assert rec_a["deliverables"][0]["paths"] == ["a.mp4"]
    assert rec_b["deliverables"][0]["paths"] == ["b.mp4"]


# ── Expired marker → new entry ────────────────────────────────────────────────

def test_expired_marker_creates_new_entry(project):
    f1 = project.root / "a.mp4"; f1.write_bytes(b"x")
    si._cmd_mark_deliverable(SimpleNamespace(file=str(f1)))

    # Forge an expired marker
    import json
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    si._batch_marker_path().write_text(
        json.dumps({"project_id": "PROJ-TEST-010125", "deliverable_index": 0, "expires_at": past}),
        encoding="utf-8",
    )

    f2 = project.root / "b.mp4"; f2.write_bytes(b"y")
    si._cmd_mark_deliverable(SimpleNamespace(file=str(f2)))

    record = _read(project.yaml_path)
    assert len(record["deliverables"]) == 2
    assert record["deliverables"][0]["paths"] == ["a.mp4"]
    assert record["deliverables"][1]["paths"] == ["b.mp4"]


# ── Negative paths ────────────────────────────────────────────────────────────

def test_missing_file_returns_error(project):
    rc = si._cmd_mark_deliverable(SimpleNamespace(file=str(project.root / "does-not-exist.mp4")))
    assert rc == 1
    assert _read(project.yaml_path)["deliverables"] == []


def test_unregistered_file_returns_error(project, tmp_path):
    """A file outside any project (find_project_for_path returns None) → no-op."""
    loose = tmp_path / "loose.mp4"
    loose.write_bytes(b"x")
    rc = si._cmd_mark_deliverable(SimpleNamespace(file=str(loose)))
    assert rc == 1
    assert _read(project.yaml_path)["deliverables"] == []


def test_paths_stored_relative_to_project_root(project):
    f = project.root / "subdir" / "asset.mp4"
    f.parent.mkdir()
    f.write_bytes(b"x")
    si._cmd_mark_deliverable(SimpleNamespace(file=str(f)))

    stored = _read(project.yaml_path)["deliverables"][0]["paths"][0]
    # Path is relative — never starts with the temp drive letter
    assert "asset.mp4" in stored
    assert stored == str(Path("subdir") / "asset.mp4") or stored == "subdir/asset.mp4"
    assert not Path(stored).is_absolute()
