"""Contract tests for the CopalVX → CopalPM subprocess boundary.

`copal_core/pm_hooks.py` invokes the external `copalpm` CLI via subprocess
at five well-defined sites:

    pre-push   copalpm record sync-time --file <yaml>
    post-push  copalpm record copalvx-update --file <yaml> --project-name <n> --version <v>
    post-pull  copalpm project register <abs_path>
    post-pull  copalpm record get copalvx.project_name --file <yaml>
    post-pull  copalpm record get copalvx.last_push    --file <yaml>

These tests verify that each subcommand exists in the installed `copalpm`
binary with the exact flag shape the hook code expects. If a future rename
of any subcommand or flag occurs in CopalPM, this test catches it before a
silent failure ships.

Auto-skips if `copalpm` is not on PATH (e.g. when CopalPM is not installed
on the machine running the test).
"""

import shutil
import subprocess

import pytest


COPALPM = shutil.which("copalpm")

if not COPALPM:
    pytest.skip(
        "copalpm binary not found on PATH. Install CopalPM with `uv tool install copalpm` "
        "(or run from a venv that has it).",
        allow_module_level=True,
    )


def _help(*args):
    """Run `copalpm <args> --help` and return the CompletedProcess.

    `--help` exits 0 on a valid command path. If any component is unknown,
    argparse exits with code 2 and writes an error to stderr.
    """
    return subprocess.run(
        [COPALPM, *args, "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )


# ── One test per pm_hooks call site ───────────────────────────────────────────

def test_pre_push_record_sync_time_exists():
    """pm_hooks.hook_pre_push → `copalpm record sync-time --file <yaml>`"""
    r = _help("record", "sync-time")
    assert r.returncode == 0, r.stderr
    assert "--file" in r.stdout, "`record sync-time` no longer accepts --file"


def test_post_push_record_copalvx_update_exists():
    """pm_hooks.hook_post_push → `copalpm record copalvx-update --file --project-name --version`"""
    r = _help("record", "copalvx-update")
    assert r.returncode == 0, r.stderr
    for flag in ("--file", "--project-name", "--version"):
        assert flag in r.stdout, (
            f"`record copalvx-update` no longer accepts {flag}. "
            f"pm_hooks.hook_post_push will silently fail."
        )


def test_post_pull_project_register_exists():
    """pm_hooks.hook_post_pull → `copalpm project register <path>`"""
    r = _help("project", "register")
    assert r.returncode == 0, r.stderr
    # The path is a positional argument; argparse renders it as "path" in help.
    assert "path" in r.stdout.lower(), (
        "`project register` no longer takes a positional path argument."
    )


def test_post_pull_record_get_exists():
    """pm_hooks.hook_post_pull → `copalpm record get <field> --file <yaml>`"""
    r = _help("record", "get")
    assert r.returncode == 0, r.stderr
    assert "field" in r.stdout.lower(), "`record get` no longer takes a positional field"
    assert "--file" in r.stdout, "`record get` no longer accepts --file"
