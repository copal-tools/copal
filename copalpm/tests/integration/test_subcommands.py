"""Integration tests that actually invoke the installed `copalpm` binary.

These complement the unit tests by exercising the full path: argparse →
dispatcher → handler → real registry. They use only read-only operations
so existing user data is never modified.

Tests auto-skip if the `copalpm` binary is not installed in the active venv.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest


# ── Binary resolution ─────────────────────────────────────────────────────────

COPALPM = Path(sys.executable).parent / (
    "copalpm.exe" if sys.platform == "win32" else "copalpm"
)

if not COPALPM.exists():
    pytest.skip(
        f"copalpm binary not found at {COPALPM}. Run `uv sync` from the repo root.",
        allow_module_level=True,
    )


def _run(*args, **kwargs):
    """Run `copalpm <args>` and return CompletedProcess. Captures output."""
    return subprocess.run(
        [str(COPALPM), *args],
        capture_output=True,
        text=True,
        timeout=15,
        **kwargs,
    )


# ── Help output ───────────────────────────────────────────────────────────────

def test_help_succeeds_and_lists_groups():
    r = _run("--help")
    assert r.returncode == 0, r.stderr
    for group in ("project", "record", "time", "service", "deliver", "tui"):
        assert group in r.stdout, f"missing group '{group}' in --help output"
    assert "task-tracker" not in r.stdout, "hidden daemon should not appear in help"
    assert "SUPPRESS" not in r.stdout


def test_each_group_has_help():
    """Every top-level group responds to `--help` cleanly."""
    for group in ("project", "record", "time", "service", "deliver", "template"):
        r = _run(group, "--help")
        assert r.returncode == 0, f"`copalpm {group} --help` failed: {r.stderr}"
        assert "usage:" in r.stdout.lower()


def test_template_group_in_help():
    """The new `template` group must surface in top-level --help."""
    r = _run("--help")
    assert r.returncode == 0, r.stderr
    assert "template" in r.stdout


# ── Read-only data commands ───────────────────────────────────────────────────

def test_project_list_runs():
    """`project list` should always succeed (empty registry → no output, still 0)."""
    r = _run("project", "list")
    assert r.returncode == 0, r.stderr


def test_project_status_runs():
    r = _run("project", "status")
    assert r.returncode == 0, r.stderr


def test_project_status_json_is_valid_json():
    r = _run("project", "status", "--json")
    assert r.returncode == 0, r.stderr
    # Output may be an empty list / object; whatever it is, it must parse.
    payload = json.loads(r.stdout)
    assert isinstance(payload, (list, dict))


def test_project_rollup_runs():
    r = _run("project", "rollup")
    assert r.returncode == 0, r.stderr


def test_template_list_runs():
    """`template list` should always succeed (auto-seeds defaults on first run)."""
    r = _run("template", "list")
    assert r.returncode == 0, r.stderr


def test_template_list_json_is_valid_json():
    r = _run("template", "list", "--json")
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert isinstance(payload, list)
    # Each entry has the documented shape
    for entry in payload:
        assert "id"      in entry
        assert "name"    in entry
        assert "fields"  in entry
        assert "folders" in entry


# ── record get round-trip (skips if registry is empty) ────────────────────────

def _first_registered_project_id():
    """Return the first project ID from the registry, or None if empty."""
    r = _run("project", "status", "--json")
    if r.returncode != 0:
        return None
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list) and payload:
        return payload[0].get("id")
    return None


def test_record_get_round_trip():
    """Resolve a project by ID, read its `name` field. Mirrors the path used
    by `pm_hooks.hook_post_pull` when reading `copalvx.project_name`."""
    pid = _first_registered_project_id()
    if not pid:
        pytest.skip("no registered projects; cannot test record get round-trip")
    r = _run("record", "get", "name", "--project", pid)
    assert r.returncode == 0, r.stderr
    # The handler prints the value followed by a newline.
    assert r.stdout.strip(), "record get returned no value"
