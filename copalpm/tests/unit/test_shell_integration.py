"""Unit tests for the shell-integration module.

Covers the pure / platform-independent surface:
- Verb definitions are well-formed and stable.
- Asset paths resolve via importlib.resources.
- Generated macOS workflow XML parses as plist.
- Generated Windows command strings quote folder paths correctly.
- File-targeted vs folder-targeted parent paths.

The actual `winreg` round-trip lives in
`tests/integration/test_shell_integration_windows.py` (Windows-gated).
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from copalpm import shell_integration as si


# ── Verb definitions ──────────────────────────────────────────────────────────

def test_verbs_have_four_entries():
    assert len(si.VERBS) == 4


def test_verbs_have_required_fields():
    required = {"id", "title", "trigger", "icon", "target"}
    for v in si.VERBS:
        assert required <= set(v.keys()), f"verb {v!r} missing keys"


def test_verb_targets_are_known():
    for v in si.VERBS:
        assert v["target"] in {"folder", "file"}, f"verb {v!r} has invalid target"


def test_verb_triggers_match_argparse():
    """The trigger field must match `shell-trigger` argparse choices."""
    triggers = {v["trigger"] for v in si.VERBS}
    assert triggers == {"start", "stop", "new-project", "mark-deliverable"}


def test_verb_ids_have_no_spaces():
    """Windows registry keys can technically take spaces but it's a footgun."""
    for v in si.VERBS:
        assert " " not in v["id"]
        assert v["id"].startswith("Copal")


def test_mark_deliverable_targets_file():
    """The file-targeted verb is the new mark-deliverable verb only."""
    verb = next(v for v in si.VERBS if v["trigger"] == "mark-deliverable")
    assert verb["target"] == "file"


def test_existing_verbs_target_folder():
    for trigger in ("start", "stop", "new-project"):
        verb = next(v for v in si.VERBS if v["trigger"] == trigger)
        assert verb["target"] == "folder"


# ── Assets ────────────────────────────────────────────────────────────────────

def test_all_icon_assets_exist():
    for v in si.VERBS:
        p = si._asset(v["icon"])
        assert p.exists(), f"missing icon: {p}"


def test_brand_icon_exists():
    """Top-level Copal.ico — currently unused by VERBS but reserved for taskbar / app id."""
    p = si._asset("copal.ico")
    assert p.exists(), f"missing brand icon: {p}"


# ── Windows command string ────────────────────────────────────────────────────

def test_win_command_string_quotes_binary_and_folder():
    s = si._win_command_string(Path(r"C:\Program Files\copal\copalpm.exe"), "start", "%1")
    assert s.startswith('"C:\\Program Files\\copal\\copalpm.exe"')
    assert s.endswith('"%1"')
    assert "shell-trigger start" in s
    assert "--folder" in s


def test_win_command_string_uses_v_placeholder():
    s = si._win_command_string(Path("copalpm.exe"), "stop", "%V")
    assert s.endswith('"%V"')


def test_win_command_string_with_file_flag():
    """File-targeted verbs use --file in the registered command."""
    s = si._win_command_string(
        Path("copalpm.exe"), "mark-deliverable", "%1", flag="--file",
    )
    assert "shell-trigger mark-deliverable" in s
    assert "--file" in s
    assert "--folder" not in s
    assert s.endswith('"%1"')


def test_win_parents_for_folder_target():
    parents = si._win_parents_for("folder")
    assert all(p.startswith(r"Software\Classes\Directory") for p in parents)
    # Both directory contexts (selected + Background) are present
    assert any("Background" in p for p in parents)
    assert any("Background" not in p for p in parents)


def test_win_parents_for_file_target_uses_asterisk_class():
    parents = si._win_parents_for("file")
    assert len(parents) == 1
    assert parents[0] == r"Software\Classes\*\shell"


def test_all_win_parents_is_union_of_targets():
    all_parents = si._all_win_parents()
    assert r"Software\Classes\Directory\shell" in all_parents
    assert r"Software\Classes\Directory\Background\shell" in all_parents
    assert r"Software\Classes\*\shell" in all_parents
    # No duplicates
    assert len(all_parents) == len(set(all_parents))


def test_win_parents_relative_path():
    """All keys are relative paths (HKLM hive selected at registration time)."""
    for parent in si._all_win_parents():
        assert parent.startswith(r"Software\Classes")
        assert "HKEY" not in parent


# ── macOS workflow XML ────────────────────────────────────────────────────────

def test_workflow_xml_parses_as_plist():
    import plistlib
    binary = Path("/usr/local/bin/copalpm")
    folder_verb = next(v for v in si.VERBS if v["target"] == "folder")
    xml = si._mac_workflow_xml(binary, folder_verb)
    # Must parse as a real plist — Automator's runtime asserts on the structure.
    parsed = plistlib.loads(xml.encode())
    assert "actions" in parsed
    assert "workflowMetaData" in parsed
    # workflowMetaData must declare itself as a service (the assertion that
    # tripped up the first hand-rolled XML: "Workflow's metaData should be
    # service metaData!" at AMWorkflowServiceRunner.m:330).
    assert parsed["workflowMetaData"]["workflowTypeIdentifier"] == "com.apple.Automator.servicesMenu"


def test_workflow_xml_contains_shell_command():
    binary = Path("/usr/local/bin/copalpm")
    folder_verb = next(v for v in si.VERBS if v["trigger"] == "stop")
    xml = si._mac_workflow_xml(binary, folder_verb)
    assert "shell-trigger stop --folder" in xml
    assert "/usr/local/bin/copalpm" in xml
    # The placeholder must be substituted out — leaving it in would make the
    # workflow shell-exec the literal string "__COPALPM_COMMAND__".
    assert "__COPALPM_COMMAND__" not in xml


def test_info_plist_parses_and_advertises_folder_input():
    import plistlib
    folder_verb = next(v for v in si.VERBS if v["target"] == "folder")
    xml = si._mac_info_plist(folder_verb)
    parsed = plistlib.loads(xml.encode())
    services = parsed["NSServices"]
    assert len(services) == 1
    svc = services[0]
    assert svc["NSMessage"] == "runWorkflowAsService"
    assert svc["NSSendFileTypes"] == ["public.folder"]
    assert svc["NSMenuItem"]["default"] == folder_verb["title"]
    # NSRequiredContext.NSApplicationIdentifier is what scopes the verb to
    # Finder. Without it, Finder will not invoke the Service.
    assert svc["NSRequiredContext"]["NSApplicationIdentifier"] == "com.apple.finder"
    assert "__MENU_TITLE__" not in xml


def test_mac_template_names_for_distinguishes_targets():
    folder_verb = next(v for v in si.VERBS if v["target"] == "folder")
    file_verb   = next(v for v in si.VERBS if v["target"] == "file")
    f_info, f_wflow = si._mac_template_names_for(folder_verb)
    assert f_info == "Info.plist.template"
    assert f_wflow == "document.wflow.template"
    file_info, file_wflow = si._mac_template_names_for(file_verb)
    assert file_info == "Info.plist.file.template"
    assert file_wflow == "document.wflow.file.template"


def test_file_templates_exist():
    """The captured Automator file templates must ship in the package."""
    assert si._mac_template_exists("Info.plist.file.template")
    assert si._mac_template_exists("document.wflow.file.template")


def test_file_workflow_xml_parses_as_plist():
    import plistlib
    binary = Path("/usr/local/bin/copalpm")
    file_verb = next(v for v in si.VERBS if v["target"] == "file")
    xml = si._mac_workflow_xml(binary, file_verb)
    parsed = plistlib.loads(xml.encode())
    assert "actions" in parsed
    assert "workflowMetaData" in parsed
    assert parsed["workflowMetaData"]["workflowTypeIdentifier"] == "com.apple.Automator.servicesMenu"


def test_file_workflow_xml_uses_file_flag_and_arg_input():
    binary = Path("/usr/local/bin/copalpm")
    file_verb = next(v for v in si.VERBS if v["target"] == "file")
    xml = si._mac_workflow_xml(binary, file_verb)
    assert f'shell-trigger {file_verb["trigger"]} --file' in xml
    assert "/usr/local/bin/copalpm" in xml
    assert "__COPALPM_COMMAND__" not in xml
    # inputMethod must be 1 (as arguments) so "$1" gets the picked file path
    # — Automator's default for "files or folders" workflows is 0 (stdin).
    import plistlib
    parsed = plistlib.loads(xml.encode())
    action_params = parsed["actions"][0]["action"]["ActionParameters"]
    assert action_params["inputMethod"] == 1, (
        "inputMethod must be 1 (arguments); 0 would pipe the path to stdin "
        "instead of substituting $1."
    )


def test_file_info_plist_parses_and_advertises_public_item():
    import plistlib
    file_verb = next(v for v in si.VERBS if v["target"] == "file")
    xml = si._mac_info_plist(file_verb)
    parsed = plistlib.loads(xml.encode())
    services = parsed["NSServices"]
    assert len(services) == 1
    svc = services[0]
    assert svc["NSMessage"] == "runWorkflowAsService"
    # `public.item` = any filesystem item (files or folders). Per Apple UTI
    # hierarchy, `public.file-url` is files-only; `public.item` is broader.
    assert svc["NSSendFileTypes"] == ["public.item"]
    assert svc["NSMenuItem"]["default"] == file_verb["title"]
    assert svc["NSRequiredContext"]["NSApplicationIdentifier"] == "com.apple.finder"
    assert "__MENU_TITLE__" not in xml


# ── Bundle paths ──────────────────────────────────────────────────────────────

def test_mac_bundle_paths_under_library_services():
    for verb in si.VERBS:
        p = si._mac_bundle_path(verb)
        assert p.name.endswith(".workflow")
        assert "Library/Services" in p.as_posix()


# ── Notifier never raises ─────────────────────────────────────────────────────

def test_notify_never_raises(monkeypatch):
    """Notifications are cosmetic — never propagate platform errors."""
    def boom(*a, **kw):
        raise RuntimeError("simulated")

    monkeypatch.setattr(si.subprocess, "run", boom)
    si._notify("title", "msg")  # must not raise
