"""Tests for the Dashboard-side wiring of `project doctor` findings.

Covers:
  - `_doctor_banner_text` singular/plural and "no drift" cases.
  - `_dashboard_rows` annotates each row with a `drift_reason` consistent
    with `find_path_drift` (consumed, not reimplemented).

The Textual widget/modal behavior is not exercised here — Textual UI testing
needs `Pilot` and is heavy for this scope. The doctor helpers themselves
are covered in test_project_doctor.py.
"""

from copalpm import tui_app
from copalpm.tui_app import _doctor_banner_text


def test_doctor_banner_text_returns_none_when_clean():
    assert _doctor_banner_text(0, 0) is None


def test_doctor_banner_text_drift_only_singular():
    text = _doctor_banner_text(1, 0)
    assert text is not None
    assert "1 stale registry entry" in text
    assert "orphan" not in text


def test_doctor_banner_text_drift_only_plural():
    text = _doctor_banner_text(3, 0)
    assert text is not None
    assert "3 stale registry entries" in text


def test_doctor_banner_text_orphans_only_singular():
    text = _doctor_banner_text(0, 1)
    assert text is not None
    assert "1 orphan session group" in text
    assert "stale registry" not in text


def test_doctor_banner_text_orphans_only_plural():
    text = _doctor_banner_text(0, 4)
    assert text is not None
    assert "4 orphan session groups" in text


def test_doctor_banner_text_combined_uses_separator():
    text = _doctor_banner_text(2, 4)
    assert text is not None
    assert "2 stale registry entries" in text
    assert "4 orphan session groups" in text
    # Both parts joined with the mid-dot separator
    assert " · " in text


def test_doctor_banner_text_invites_d_key():
    text = _doctor_banner_text(1, 0)
    assert text is not None
    assert "D" in text


def test_dashboard_rows_annotates_drift_reason(tmp_path, monkeypatch):
    """A registry with one healthy and two broken entries should yield
    rows whose `drift_reason` matches what `find_path_drift` returns."""
    ok_dir     = tmp_path / "ok"
    noyaml_dir = tmp_path / "noyaml"
    missing    = tmp_path / "gone"  # not created

    ok_dir.mkdir()
    (ok_dir / "project.yaml").write_text("id: PROJ-ok-010125\n", encoding="utf-8")
    noyaml_dir.mkdir()  # exists but no project.yaml inside

    registry = [
        {"id": "PROJ-ok-010125",     "name": "OK",     "path": str(ok_dir)},
        {"id": "PROJ-gone-020225",   "name": "Gone",   "path": str(missing)},
        {"id": "PROJ-noyaml-030325", "name": "NoYaml", "path": str(noyaml_dir)},
    ]

    monkeypatch.setattr(tui_app, "load_registry", lambda: registry)
    # _dashboard_rows reads project.yaml for non-stale rows; for the OK row
    # the real loader works fine — just leave it.

    rows = tui_app._dashboard_rows()
    by_id = {r["id"]: r for r in rows}

    assert by_id["PROJ-ok-010125"]["drift_reason"]     is None
    assert by_id["PROJ-gone-020225"]["drift_reason"]   == "missing_path"
    assert by_id["PROJ-noyaml-030325"]["drift_reason"] == "missing_yaml"


def test_dashboard_rows_clean_registry_has_no_drift(tmp_path, monkeypatch):
    """A registry with all-healthy entries leaves drift_reason None on every row."""
    p = tmp_path / "ok"
    p.mkdir()
    (p / "project.yaml").write_text("id: PROJ-ok-010125\n", encoding="utf-8")

    monkeypatch.setattr(
        tui_app, "load_registry",
        lambda: [{"id": "PROJ-ok-010125", "name": "OK", "path": str(p)}],
    )

    rows = tui_app._dashboard_rows()
    assert len(rows) == 1
    assert rows[0]["drift_reason"] is None
