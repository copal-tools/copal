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
    binary = Path("/usr/local/bin/copalpm")
    xml = si._mac_workflow_xml(binary, "start")
    # Must be valid XML — Automator refuses malformed plist.
    root = ET.fromstring(xml)
    assert root.tag == "plist"


def test_workflow_xml_contains_shell_command():
    binary = Path("/usr/local/bin/copalpm")
    xml = si._mac_workflow_xml(binary, "stop")
    assert "shell-trigger stop --folder" in xml
    assert "/usr/local/bin/copalpm" in xml


def test_info_plist_parses_and_advertises_folder_input():
    verb = si.VERBS[0]
    xml = si._mac_info_plist(verb)
    root = ET.fromstring(xml)
    assert root.tag == "plist"
    # Surface-level sanity: the folder-type marker and the menu title are present.
    assert "public.folder" in xml
    assert verb["title"] in xml


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
