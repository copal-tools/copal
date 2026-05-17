"""Import-smoke test for the AddDeliverableModal.

Real interaction testing belongs in a Textual app-pilot harness; this
suite just guards against the class disappearing or losing its public
shape under future refactors.
"""

from pathlib import Path

from copalpm import tui_app


def test_add_deliverable_modal_imports():
    assert hasattr(tui_app, "AddDeliverableModal")


def test_add_deliverable_modal_constructable():
    modal = tui_app.AddDeliverableModal(Path.cwd())
    assert modal._project_root == Path.cwd()
    assert modal._paths == []
    assert modal._user_edited_name is False


def test_add_deliverable_modal_has_compose():
    assert callable(getattr(tui_app.AddDeliverableModal, "compose", None))


def test_add_deliverable_modal_has_on_button_pressed():
    assert callable(getattr(tui_app.AddDeliverableModal, "on_button_pressed", None))


def test_files_summary_pluralizes():
    modal = tui_app.AddDeliverableModal(Path.cwd())
    assert "0 files" in modal._files_summary()
    modal._paths = [Path("a.mp4")]
    assert "1 file" in modal._files_summary()
    assert "1 files" not in modal._files_summary()
    modal._paths = [Path("a.mp4"), Path("b.mp4")]
    assert "2 files" in modal._files_summary()
