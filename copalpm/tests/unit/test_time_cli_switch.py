"""`copalpm time start` should surface the auto-stop of any prior session.

The daemon's `/start` response now carries an optional `stopped_prev` block
(see test_task_tracker_switch.py). `cmd_start` reads it and prints a
one-line "■ Stopped <name> (<id>) — <duration> logged" before the usual
"▶ <new>" line.

These tests mock `_api` so they exercise only the formatting logic.
"""

from argparse import Namespace

from copalpm import time_cli


def _args(**overrides) -> Namespace:
    base = {
        "description": None,
        "tool":        None,
        "phase":       None,
        "project":     "PROJ-NEW-010125",  # bypass cwd walk
    }
    base.update(overrides)
    return Namespace(**base)


def test_cmd_start_prints_only_started_when_no_prior_session(monkeypatch, capsys):
    monkeypatch.setattr(
        time_cli, "_api",
        lambda *a, **kw: {"ok": True, "session_id": "S-x"},
    )
    monkeypatch.setattr(time_cli, "current_phase_from_cwd", lambda: None)

    time_cli.cmd_start(_args())
    out = capsys.readouterr().out

    assert "▶" in out
    assert "PROJ-NEW-010125" in out
    assert "Stopped" not in out


def test_cmd_start_prints_stopped_prev_when_present(monkeypatch, capsys):
    monkeypatch.setattr(
        time_cli, "_api",
        lambda *a, **kw: {
            "ok":           True,
            "session_id":   "S-x",
            "stopped_prev": {"project_id": "PROJ-OLD-010125", "duration_sec": 3690},
        },
    )
    monkeypatch.setattr(time_cli, "current_phase_from_cwd", lambda: None)
    monkeypatch.setattr(time_cli, "_project_name", lambda pid: "Old-Name")

    time_cli.cmd_start(_args())
    out = capsys.readouterr().out

    # ■ Stopped Old-Name (PROJ-OLD-010125) — 1h 01m logged
    assert "■" in out
    assert "Stopped" in out
    assert "Old-Name" in out
    assert "PROJ-OLD-010125" in out
    assert "1h 01m" in out
    # The "started" line still appears, after the stopped one
    out_lines = out.splitlines()
    stopped_idx = next(i for i, l in enumerate(out_lines) if "Stopped" in l)
    started_idx = next(i for i, l in enumerate(out_lines) if "▶" in l)
    assert stopped_idx < started_idx


def test_cmd_start_handles_short_duration(monkeypatch, capsys):
    monkeypatch.setattr(
        time_cli, "_api",
        lambda *a, **kw: {
            "ok":           True,
            "session_id":   "S-x",
            "stopped_prev": {"project_id": "PROJ-OLD-010125", "duration_sec": 45},
        },
    )
    monkeypatch.setattr(time_cli, "current_phase_from_cwd", lambda: None)
    monkeypatch.setattr(time_cli, "_project_name", lambda pid: pid)

    time_cli.cmd_start(_args())
    out = capsys.readouterr().out
    # fmt_duration(45) returns "0m" because seconds // 60 == 0
    assert "0m logged" in out
