"""Tests for `project_doctor` helpers.

Both `find_path_drift` and `find_orphan_sessions` take their inputs
explicitly so we can drive them with a `tmp_path` fixture without
touching the real user data directory.
"""

import json
from pathlib import Path

import pytest

from copalpm.project_doctor import find_path_drift, find_orphan_sessions


@pytest.fixture
def drift_scenario(tmp_path):
    """Three registered projects with mixed on-disk health, plus a
    sessions log carrying one valid + three orphan + one malformed line."""
    ok_dir     = tmp_path / "ok"
    noyaml_dir = tmp_path / "noyaml"
    # missing_dir intentionally not created
    missing_dir = tmp_path / "missing"

    ok_dir.mkdir()
    (ok_dir / "project.yaml").write_text("id: PROJ-ok-010125\n", encoding="utf-8")
    noyaml_dir.mkdir()  # no project.yaml inside

    registry = [
        {"id": "PROJ-ok-010125",     "name": "OK",     "path": str(ok_dir)},
        {"id": "PROJ-gone-020225",   "name": "Gone",   "path": str(missing_dir)},
        {"id": "PROJ-noyaml-030325", "name": "NoYaml", "path": str(noyaml_dir)},
    ]

    sessions = tmp_path / "sessions.jsonl"
    sessions.write_text(
        json.dumps({"session_id": "s1", "project_id": "PROJ-ok-010125"})       + "\n"
        + json.dumps({"session_id": "s2", "project_id": "PROJ-orphan-040425"}) + "\n"
        + json.dumps({"session_id": "s3", "project_id": "PROJ-orphan-040425"}) + "\n"
        + json.dumps({"session_id": "s4", "project_id": "PROJ-orphan-040425"}) + "\n"
        + "not-json\n",
        encoding="utf-8",
    )

    return {"registry": registry, "sessions": sessions, "tmp_path": tmp_path}


def test_find_path_drift_reports_missing_folder_and_missing_yaml(drift_scenario):
    drift = find_path_drift(drift_scenario["registry"])

    by_id = {d["id"]: d for d in drift}
    assert set(by_id) == {"PROJ-gone-020225", "PROJ-noyaml-030325"}
    assert by_id["PROJ-gone-020225"]["reason"]   == "missing_path"
    assert by_id["PROJ-noyaml-030325"]["reason"] == "missing_yaml"


def test_find_path_drift_handles_entry_without_path():
    registry = [{"id": "PROJ-pathless-010125", "name": "Pathless"}]
    drift    = find_path_drift(registry)
    assert len(drift) == 1
    assert drift[0]["id"]     == "PROJ-pathless-010125"
    assert drift[0]["reason"] == "missing_path"


def test_find_orphan_sessions_groups_unregistered_project_ids(drift_scenario):
    orphans = find_orphan_sessions(drift_scenario["registry"], drift_scenario["sessions"])
    assert orphans == {"PROJ-orphan-040425": 3}


def test_find_orphan_sessions_returns_empty_when_log_missing(tmp_path):
    nonexistent = tmp_path / "no-such-file.jsonl"
    assert find_orphan_sessions([], nonexistent) == {}


def test_find_orphan_sessions_returns_empty_when_log_empty(tmp_path):
    empty = tmp_path / "sessions.jsonl"
    empty.write_text("", encoding="utf-8")
    assert find_orphan_sessions([{"id": "PROJ-x-010125"}], empty) == {}


def test_find_orphan_sessions_skips_malformed_json_lines(tmp_path):
    sessions = tmp_path / "sessions.jsonl"
    sessions.write_text(
        "not-json\n"
        + json.dumps({"project_id": "PROJ-orphan-010125"}) + "\n"
        + "{also not json\n"
        + json.dumps({"project_id": "PROJ-orphan-010125"}) + "\n",
        encoding="utf-8",
    )
    assert find_orphan_sessions([], sessions) == {"PROJ-orphan-010125": 2}


def test_find_orphan_sessions_ignores_known_project_ids(drift_scenario):
    # All three registered ids should be filtered out; only PROJ-orphan-040425 surfaces.
    orphans = find_orphan_sessions(drift_scenario["registry"], drift_scenario["sessions"])
    assert "PROJ-ok-010125"     not in orphans
    assert "PROJ-gone-020225"   not in orphans
    assert "PROJ-noyaml-030325" not in orphans
