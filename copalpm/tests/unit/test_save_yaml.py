"""Tests for the atomic project_record.save_yaml write path.

Covers the round-trip, header preservation, tmp-file cleanup, overwrite
behaviour, concurrent-write safety, and the Windows ERROR_SHARING_VIOLATION
retry harness.
"""

import os
import sys
import threading

import pytest
import yaml

from copalpm import project_record
from copalpm.project_record import _YAML_HEADER, load_yaml, save_yaml


def _tmp_siblings(path):
    return [p for p in path.parent.iterdir() if ".tmp." in p.name]


def test_round_trip(tmp_path):
    p = tmp_path / "project.yaml"
    record = {"id": "PROJ-X", "name": "Y", "phase_log": [{"phase": "concept"}]}
    save_yaml(p, record)
    assert load_yaml(p) == record


def test_header_preserved(tmp_path):
    p = tmp_path / "project.yaml"
    save_yaml(p, {"id": "X"})
    assert p.read_text(encoding="utf-8").startswith(_YAML_HEADER)


def test_tmp_file_cleaned_up_on_success(tmp_path):
    p = tmp_path / "project.yaml"
    save_yaml(p, {"id": "X"})
    assert _tmp_siblings(p) == []


def test_overwrites_existing_file(tmp_path):
    p = tmp_path / "project.yaml"
    save_yaml(p, {"id": "A"})
    save_yaml(p, {"id": "B"})
    assert load_yaml(p) == {"id": "B"}


def test_concurrent_writes_stay_parseable(tmp_path):
    """Two threads hammering the same file must never leave it half-written.

    With the pre-atomic implementation a reader observing mid-write could see
    a truncated buffer; with tmp + os.replace each writer's content lands as
    one complete YAML document.
    """
    p = tmp_path / "project.yaml"
    save_yaml(p, {"id": "seed"})
    errors = []

    def writer(tag):
        try:
            for i in range(50):
                save_yaml(p, {"id": tag, "iteration": i, "payload": "x" * 200})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=writer, args=("A",))
    t2 = threading.Thread(target=writer, args=("B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == [], f"writer raised: {errors!r}"
    final = load_yaml(p)
    assert isinstance(final, dict)
    assert final.get("id") in {"A", "B"}
    assert _tmp_siblings(p) == []


@pytest.mark.skipif(sys.platform != "win32", reason="retry loop is Windows-only")
def test_windows_sharing_violation_is_retried(tmp_path, monkeypatch):
    p = tmp_path / "project.yaml"
    save_yaml(p, {"id": "before"})

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            err = OSError(13, "sharing violation")
            err.winerror = 32
            raise err
        return real_replace(src, dst)

    monkeypatch.setattr(project_record.os, "replace", flaky_replace)
    monkeypatch.setattr(project_record.time, "sleep", lambda _: None)

    save_yaml(p, {"id": "after"})

    assert calls["n"] == 2
    assert load_yaml(p) == {"id": "after"}
    assert _tmp_siblings(p) == []
