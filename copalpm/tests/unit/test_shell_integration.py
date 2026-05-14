"""Unit tests for the shell-integration module.

Covers the pure / platform-independent surface:
- Verb definitions are well-formed and stable.
- Asset paths resolve via importlib.resources.
- Generated macOS workflow XML parses as plist.
- Generated Windows command strings quote folder paths correctly.

The actual `winreg` round-trip lives in
`tests/integration/test_shell_integration_windows.py` (Windows-gated).
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from copalpm import shell_integration as si


# ── Verb definitions ──────────────────────────────────────────────────────────

def test_verbs_have_three_entries():
    assert len(si.VERBS) == 3


def test_verbs_have_required_fields():
    required = {"id", "title", "trigger", "icon"}
    for v in si.VERBS:
        assert required <= set(v.keys()), f"verb {v!r} missing keys"


def test_verb_triggers_match_argparse():
    """The trigger field must match `shell-trigger` argparse choices."""
    triggers = {v["trigger"] for v in si.VERBS}
    assert triggers == {"start", "stop", "new-project"}


def test_verb_ids_have_no_spaces():
    """Windows registry keys can technically take spaces but it's a footgun."""
    for v in si.VERBS:
        assert " " not in v["id"]
        assert v["id"].startswith("Copal")


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


def test_win_command_string_uses_v_placeholder():
    s = si._win_command_string(Path("copalpm.exe"), "stop", "%V")
    assert s.endswith('"%V"')


def test_win_parents_are_hkcu_relative():
    """All keys must live under HKCU — installing without admin is non-negotiable."""
    for parent in si._WIN_PARENTS:
        assert parent.startswith(r"Software\Classes\Directory")
        assert "HKEY" not in parent  # path is relative to HKCU


# ── macOS workflow XML ────────────────────────────────────────────────────────

def test_workflow_xml_parses_as_plist():
    import plistlib
    binary = Path("/usr/local/bin/copalpm")
    xml = si._mac_workflow_xml(binary, "start")
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
    xml = si._mac_workflow_xml(binary, "stop")
    assert "shell-trigger stop --folder" in xml
    assert "/usr/local/bin/copalpm" in xml
    # The placeholder must be substituted out — leaving it in would make the
    # workflow shell-exec the literal string "__COPALPM_COMMAND__".
    assert "__COPALPM_COMMAND__" not in xml


def test_info_plist_parses_and_advertises_folder_input():
    import plistlib
    verb = si.VERBS[0]
    xml = si._mac_info_plist(verb)
    parsed = plistlib.loads(xml.encode())
    services = parsed["NSServices"]
    assert len(services) == 1
    svc = services[0]
    assert svc["NSMessage"] == "runWorkflowAsService"
    assert svc["NSSendFileTypes"] == ["public.folder"]
    assert svc["NSMenuItem"]["default"] == verb["title"]
    # NSRequiredContext.NSApplicationIdentifier is what scopes the verb to
    # Finder. Without it, Finder will not invoke the Service.
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
