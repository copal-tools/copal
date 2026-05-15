"""Behavioural tests for time_cli handlers that don't talk to the daemon.

`cmd_log` writes a manual time entry directly to `project.yaml` via
`save_yaml`, no HTTP service required — so it's exercisable in a pure
unit test with `monkeypatch.chdir(tmp_path)`.
"""

from argparse import Namespace

from copalpm.project_record import load_yaml, save_yaml
from copalpm.time_cli import cmd_log


def _seed_project(path):
    save_yaml(path, {
        "id": "PROJ-TEST-010125",
        "name": "Test",
        "phase_log": [{"phase": "production", "entered_at": "2026-01-01T00:00:00Z"}],
    })


def test_cmd_log_appends_entry(tmp_path, monkeypatch, capsys):
    yaml_path = tmp_path / "project.yaml"
    _seed_project(yaml_path)
    monkeypatch.chdir(tmp_path)

    cmd_log(Namespace(duration_min=45, description="client call", phase=None, tool=None))

    record = load_yaml(yaml_path)
    entries = record.get("time_entries", [])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["duration_sec"] == 45 * 60
    assert entry["description"] == "client call"
    assert entry["phase"] == "production"
    assert entry["stop_reason"] == "manual_log"
    assert entry["session_id"].startswith("M-")

    out = capsys.readouterr().out
    assert "Logged 45 min" in out
    assert "client call" in out


def test_cmd_log_inherits_phase_from_record(tmp_path, monkeypatch, capsys):
    yaml_path = tmp_path / "project.yaml"
    save_yaml(yaml_path, {
        "id": "PROJ-X",
        "phase_log": [
            {"phase": "concept", "entered_at": "2026-01-01T00:00:00Z"},
            {"phase": "delivery", "entered_at": "2026-02-01T00:00:00Z"},
        ],
    })
    monkeypatch.chdir(tmp_path)

    cmd_log(Namespace(duration_min=10, description="x", phase=None, tool=None))

    entry = load_yaml(yaml_path)["time_entries"][0]
    assert entry["phase"] == "delivery"


def test_cmd_log_leaves_no_tmp_file(tmp_path, monkeypatch, capsys):
    yaml_path = tmp_path / "project.yaml"
    _seed_project(yaml_path)
    monkeypatch.chdir(tmp_path)

    cmd_log(Namespace(duration_min=5, description="quick", phase=None, tool="aftereffects"))

    siblings = [p.name for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert siblings == []


def test_cmd_log_writes_yaml_header(tmp_path, monkeypatch, capsys):
    """Refactor regression guard: the inlined header has been removed and
    save_yaml is the sole writer; the on-disk header must still be present.
    """
    yaml_path = tmp_path / "project.yaml"
    _seed_project(yaml_path)
    monkeypatch.chdir(tmp_path)

    cmd_log(Namespace(duration_min=1, description="hdr", phase=None, tool=None))

    text = yaml_path.read_text(encoding="utf-8")
    assert text.startswith("# project.yaml — Project Record v1")
