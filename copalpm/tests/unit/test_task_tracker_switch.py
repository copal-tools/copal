"""Daemon-side contract test for the switch-stop behavior.

When a `/start` call switches from project A to project B, the response now
includes a `stopped_prev` block carrying A's id + duration so clients (TUI,
shell-trigger, CLI) can render a "■ Stopped A — Xm logged" toast in addition
to the new "● Started B" one.

We exercise the Flask `app` directly via its test_client instead of spawning
the binary. Module-level path constants and `cfg` are monkeypatched to point
at tmp files; `_reset_idle_timer` is replaced with a no-op so background
threading.Timer objects don't leak across tests.
"""

import json

from copalpm import task_tracker


def _setup(tmp_path, monkeypatch):
    cur = tmp_path / "current.json"
    log = tmp_path / "sessions.jsonl"
    reg = tmp_path / "registry.json"

    reg.write_text(
        json.dumps([
            {"id": "PROJ-AAA-010125", "name": "Alpha", "path": str(tmp_path)},
            {"id": "PROJ-BBB-020225", "name": "Beta",  "path": str(tmp_path)},
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(task_tracker, "CUR", str(cur))
    monkeypatch.setattr(task_tracker, "LOG", str(log))
    monkeypatch.setattr(task_tracker, "REG", str(reg))
    monkeypatch.setattr(task_tracker, "cfg", {"api_key": "test-key", "idle_minutes": 999})
    monkeypatch.setattr(task_tracker, "_REG_CACHE", {"mtime": None, "ids": set()})
    monkeypatch.setattr(task_tracker, "_reset_idle_timer", lambda: None)

    return task_tracker.app.test_client()


def test_start_without_prior_session_omits_stopped_prev(tmp_path, monkeypatch):
    client  = _setup(tmp_path, monkeypatch)
    headers = {"X-API-Key": "test-key"}

    r = client.post("/start", json={"projectId": "PROJ-AAA-010125"}, headers=headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert "stopped_prev" not in body


def test_start_after_running_session_includes_stopped_prev(tmp_path, monkeypatch):
    client  = _setup(tmp_path, monkeypatch)
    headers = {"X-API-Key": "test-key"}

    r1 = client.post("/start", json={"projectId": "PROJ-AAA-010125"}, headers=headers)
    assert r1.status_code == 200
    assert "stopped_prev" not in r1.get_json()

    r2 = client.post("/start", json={"projectId": "PROJ-BBB-020225"}, headers=headers)
    assert r2.status_code == 200
    body2 = r2.get_json()
    assert "stopped_prev" in body2
    sp = body2["stopped_prev"]
    assert sp["project_id"]   == "PROJ-AAA-010125"
    assert "duration_sec" in sp
    assert isinstance(sp["duration_sec"], int)
    assert sp["duration_sec"] >= 0


def test_start_unknown_project_does_not_stop_current(tmp_path, monkeypatch):
    """Starting on an unknown id should 404 and leave the existing session alone."""
    client  = _setup(tmp_path, monkeypatch)
    headers = {"X-API-Key": "test-key"}

    client.post("/start", json={"projectId": "PROJ-AAA-010125"}, headers=headers)
    r = client.post("/start", json={"projectId": "PROJ-XXX-999999"}, headers=headers)
    assert r.status_code == 404

    # Current session should still be Alpha
    s = client.get("/state", headers=headers)
    assert s.status_code == 200
    assert s.get_json()["project_id"] == "PROJ-AAA-010125"
