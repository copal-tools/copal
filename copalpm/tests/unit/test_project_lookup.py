"""Tests for `find_project_for_path` and the `copalpm whose` CLI handler.

The pure helper takes the registry as an explicit list[dict], so most tests
can build a synthetic registry pointing at folders created under `tmp_path`
without monkeypatching `load_registry`. The CLI handler tests use
`monkeypatch.setattr` to substitute the registry loader for two end-to-end
exit-code / output-shape checks.
"""

import json
import os
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from copalpm import project_lookup
from copalpm.project_lookup import (
    ProjectMatch,
    _is_under,
    _norm,
    cmd_whose,
    find_project_for_path,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(folder: Path, project_id: str, name: str = "") -> Path:
    """Create folder/project.yaml with the given id. Returns the folder path."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project.yaml").write_text(
        yaml.safe_dump({"id": project_id, "name": name or project_id}),
        encoding="utf-8",
    )
    return folder


def _registry_entry(pid: str, name: str, path: Path) -> dict:
    return {"id": pid, "name": name, "path": str(path), "registered_at": "2026-01-01T00:00:00Z"}


# ── _is_under prefix-anchor semantics ────────────────────────────────────────

def test_is_under_equal_paths_match():
    assert _is_under(_norm(Path("/work/alpha")), _norm(Path("/work/alpha")))


def test_is_under_descendant_matches():
    child = _norm(Path("/work/alpha/src/main.py"))
    root  = _norm(Path("/work/alpha"))
    assert _is_under(child, root)


def test_is_under_sibling_with_prefix_string_does_not_match():
    # Guards against the classic startswith() bug:
    # `C:\Work\Alpha` is a string prefix of `C:\Work\AlphaBeta`, but a different folder.
    child = _norm(Path("/work/alphabeta/foo.txt"))
    root  = _norm(Path("/work/alpha"))
    assert not _is_under(child, root)


# ── Pass 1: registry-prefix matches ──────────────────────────────────────────

def test_file_inside_registered_project_returns_registry_match(tmp_path):
    proj = _make_project(tmp_path / "alpha", "PROJ-ALPHA-010125", "Alpha")
    registry = [_registry_entry("PROJ-ALPHA-010125", "Alpha", proj)]

    match = find_project_for_path(proj / "src" / "main.py", registry=registry)

    assert match is not None
    assert match.project_id   == "PROJ-ALPHA-010125"
    assert match.project_name == "Alpha"
    assert match.drift is False
    assert match.matched_via  == "registry"


def test_folder_itself_is_registered_root(tmp_path):
    proj = _make_project(tmp_path / "alpha", "PROJ-A-010125", "Alpha")
    registry = [_registry_entry("PROJ-A-010125", "Alpha", proj)]

    match = find_project_for_path(proj, registry=registry)
    assert match is not None
    assert match.project_id == "PROJ-A-010125"
    assert match.drift is False


def test_nested_projects_deepest_match_wins(tmp_path):
    alpha = _make_project(tmp_path / "alpha", "PROJ-A-010125", "Alpha")
    beta  = _make_project(alpha / "beta",     "PROJ-B-010125", "Beta")
    registry = [
        _registry_entry("PROJ-A-010125", "Alpha", alpha),
        _registry_entry("PROJ-B-010125", "Beta",  beta),
    ]

    match = find_project_for_path(beta / "src" / "main.py", registry=registry)
    assert match is not None
    assert match.project_id == "PROJ-B-010125"


def test_non_existent_file_under_registered_root_still_matches(tmp_path):
    proj = _make_project(tmp_path / "alpha", "PROJ-A-010125")
    registry = [_registry_entry("PROJ-A-010125", "Alpha", proj)]

    match = find_project_for_path(proj / "deeply" / "nested" / "never-existed.py",
                                  registry=registry)
    assert match is not None
    assert match.project_id == "PROJ-A-010125"


# ── Misses ───────────────────────────────────────────────────────────────────

def test_file_outside_all_projects_returns_none(tmp_path):
    proj = _make_project(tmp_path / "alpha", "PROJ-A-010125")
    registry = [_registry_entry("PROJ-A-010125", "Alpha", proj)]

    elsewhere = tmp_path / "outside" / "random.txt"
    elsewhere.parent.mkdir()
    elsewhere.write_text("x")

    assert find_project_for_path(elsewhere, registry=registry) is None


def test_unregistered_project_yaml_returns_none(tmp_path):
    # project.yaml exists on disk but its id is NOT in the registry → None
    _make_project(tmp_path / "ghost", "PROJ-GHOST-010125")
    registry: list[dict] = []  # empty registry

    assert find_project_for_path(tmp_path / "ghost" / "src.py",
                                 registry=registry) is None


def test_empty_registry_returns_none(tmp_path):
    elsewhere = tmp_path / "anywhere.txt"
    elsewhere.write_text("x")
    assert find_project_for_path(elsewhere, registry=[]) is None


# ── Pass 2: walk-up drift recovery ───────────────────────────────────────────

def test_drift_recovery_renamed_folder(tmp_path):
    # Registry says /tmp/old-name; folder was actually renamed to /tmp/new-name.
    # The YAML inside new-name carries the same id → Pass 2 should recover.
    new_folder = _make_project(tmp_path / "new-name", "PROJ-MOVED-010125", "Moved")
    registry   = [_registry_entry("PROJ-MOVED-010125", "Moved",
                                  tmp_path / "old-name")]  # stale path; never created

    match = find_project_for_path(new_folder / "src" / "main.py", registry=registry)

    assert match is not None
    assert match.project_id   == "PROJ-MOVED-010125"
    assert match.project_name == "Moved"
    assert match.drift is True
    assert match.matched_via  == "walk-up"
    # Drift case: project_root is the YAML's actual parent, not the stale registry path
    assert _norm(match.project_root) == _norm(new_folder)


def test_walk_up_finds_yaml_but_id_unregistered_returns_none(tmp_path):
    # project.yaml is on disk but its id is not in registry → strict miss.
    folder = _make_project(tmp_path / "orphan", "PROJ-NOTREG-010125")
    other  = _make_project(tmp_path / "other",  "PROJ-OTHER-010125")
    registry = [_registry_entry("PROJ-OTHER-010125", "Other", other)]

    assert find_project_for_path(folder / "src.py", registry=registry) is None


def test_malformed_yaml_during_walk_up_returns_none(tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "project.yaml").write_text(": : not valid yaml :::\n", encoding="utf-8")
    registry = [_registry_entry("PROJ-ANY-010125", "Any", tmp_path / "elsewhere")]

    assert find_project_for_path(folder / "src.py", registry=registry) is None


def test_walk_up_yaml_without_id_returns_none(tmp_path):
    folder = tmp_path / "idless"
    folder.mkdir()
    (folder / "project.yaml").write_text(yaml.safe_dump({"name": "no id"}),
                                          encoding="utf-8")
    registry: list[dict] = []
    assert find_project_for_path(folder / "src.py", registry=registry) is None


# ── Platform-specific normalization ──────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32",
                    reason="Windows case-insensitive path matching")
def test_case_insensitive_match_on_windows(tmp_path):
    proj = _make_project(tmp_path / "Alpha", "PROJ-A-010125")
    registry = [_registry_entry("PROJ-A-010125", "Alpha", proj)]

    # Query with a different case for the folder segment
    weird_case = Path(str(proj).replace("Alpha", "ALPHA")) / "file.py"
    match = find_project_for_path(weird_case, registry=registry)
    assert match is not None
    assert match.project_id == "PROJ-A-010125"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX is case-sensitive")
def test_case_sensitive_match_on_posix(tmp_path):
    proj = _make_project(tmp_path / "Alpha", "PROJ-A-010125")
    registry = [_registry_entry("PROJ-A-010125", "Alpha", proj)]

    # Different case = different path on POSIX → must NOT match
    elsewhere = tmp_path / "ALPHA"
    assert find_project_for_path(elsewhere / "file.py", registry=registry) is None


# ── CLI handler ──────────────────────────────────────────────────────────────

def test_cmd_whose_human_output_match(tmp_path, capsys, monkeypatch):
    proj = _make_project(tmp_path / "alpha", "PROJ-A-010125", "Alpha")
    registry = [_registry_entry("PROJ-A-010125", "Alpha", proj)]
    monkeypatch.setattr(project_lookup, "load_registry", lambda: registry)

    cmd_whose(Namespace(path=str(proj / "main.py"), json=False))

    out = capsys.readouterr().out
    assert "PROJ-A-010125" in out
    assert "Alpha" in out
    assert "root:" in out
    assert "via:  registry" in out


def test_cmd_whose_human_output_miss_exits_one(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(project_lookup, "load_registry", lambda: [])

    with pytest.raises(SystemExit) as excinfo:
        cmd_whose(Namespace(path=str(tmp_path / "nowhere.txt"), json=False))
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "not in any registered project" in err


def test_cmd_whose_json_match(tmp_path, capsys, monkeypatch):
    proj = _make_project(tmp_path / "alpha", "PROJ-A-010125", "Alpha")
    registry = [_registry_entry("PROJ-A-010125", "Alpha", proj)]
    monkeypatch.setattr(project_lookup, "load_registry", lambda: registry)

    cmd_whose(Namespace(path=str(proj / "main.py"), json=True))

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["project_id"]   == "PROJ-A-010125"
    assert data["project_name"] == "Alpha"
    assert data["drift"]        is False
    assert data["matched_via"]  == "registry"
    # project_root is a string in the JSON shape
    assert isinstance(data["project_root"], str)


def test_cmd_whose_json_miss_emits_null_and_exits_one(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(project_lookup, "load_registry", lambda: [])

    with pytest.raises(SystemExit) as excinfo:
        cmd_whose(Namespace(path=str(tmp_path / "nowhere.txt"), json=True))
    assert excinfo.value.code == 1
    assert capsys.readouterr().out.strip() == "null"


def test_cmd_whose_drift_human_output_includes_drift_marker(tmp_path, capsys, monkeypatch):
    new_folder = _make_project(tmp_path / "renamed", "PROJ-MOVED-010125", "Moved")
    registry   = [_registry_entry("PROJ-MOVED-010125", "Moved",
                                  tmp_path / "stale-original")]
    monkeypatch.setattr(project_lookup, "load_registry", lambda: registry)

    cmd_whose(Namespace(path=str(new_folder / "x.py"), json=False))

    out = capsys.readouterr().out
    assert "(drift)" in out
    assert "via:  walk-up" in out
