"""Argparse dispatcher tests for `copalpm`.

Builds the parser via `cli._build_parser()` and parses every documented
invocation against it. Catches:

- A subcommand path renamed without updating cli.py
- A required argument removed / mis-named
- Help text accidentally exposing the hidden `task-tracker` subcommand
- The "no args → TUI" default behavior regressing

Fast (< 1s), no subprocess, no external state.
"""

import pytest
import argparse

from copalpm import cli


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse(argv):
    """Parse argv with a fresh parser; surface SystemExit cleanly."""
    return cli._build_parser().parse_args(argv)


# ── Top-level groups ──────────────────────────────────────────────────────────

def test_no_args_yields_no_group():
    """Bare `copalpm` → main() routes None to the TUI launcher."""
    args = _parse([])
    assert args.group is None


def test_explicit_tui_group():
    args = _parse(["tui"])
    assert args.group == "tui"


def test_task_tracker_group_parseable():
    """Hidden from --help, but still a valid subcommand."""
    args = _parse(["task-tracker"])
    assert args.group == "task-tracker"


def test_unknown_group_exits():
    with pytest.raises(SystemExit):
        _parse(["nonsense"])


# ── `project` group ───────────────────────────────────────────────────────────

def test_project_init_minimal():
    args = _parse(["project", "init", "MyProject"])
    assert args.group == "project"
    assert args.cmd == "init"
    assert args.name == "MyProject"
    assert args.tactical is False
    assert args.ds is False
    assert args.inc is False


def test_project_init_with_flags():
    args = _parse(["project", "init", "MyProj", "--dir", "/tmp", "--inc", "--tactical"])
    assert args.dir == "/tmp"
    assert args.inc is True
    assert args.tactical is True


def test_project_init_tactical_and_ds_mutually_exclusive():
    with pytest.raises(SystemExit):
        _parse(["project", "init", "X", "--tactical", "--ds"])


def test_project_init_requires_name():
    with pytest.raises(SystemExit):
        _parse(["project", "init"])


def test_project_list():
    args = _parse(["project", "list"])
    assert args.group == "project" and args.cmd == "list"


def test_project_status_json_flag():
    args = _parse(["project", "status", "--json"])
    assert args.json is True


def test_project_rollup_json_flag():
    args = _parse(["project", "rollup", "--json"])
    assert args.json is True


def test_project_register_requires_path():
    with pytest.raises(SystemExit):
        _parse(["project", "register"])


def test_project_register_with_path():
    args = _parse(["project", "register", "C:/foo"])
    assert args.path == "C:/foo"


def test_project_scan_with_dir():
    args = _parse(["project", "scan", "C:/projects"])
    assert args.directory == "C:/projects"


def test_project_remove_with_id():
    args = _parse(["project", "remove", "PROJ-FOO-010101"])
    assert args.project_id == "PROJ-FOO-010101"


def test_project_doctor():
    args = _parse(["project", "doctor"])
    assert args.group == "project" and args.cmd == "doctor"


# ── `record` group ────────────────────────────────────────────────────────────

def test_record_show():
    args = _parse(["record", "show"])
    assert args.group == "record" and args.cmd == "show"


def test_record_get_requires_field():
    with pytest.raises(SystemExit):
        _parse(["record", "get"])


def test_record_get_with_field_and_file():
    args = _parse(["record", "get", "deadline", "--file", "C:/p/project.yaml"])
    assert args.field == "deadline"
    assert args.file == "C:/p/project.yaml"


def test_record_get_with_project_id():
    args = _parse(["record", "get", "name", "--project", "PROJ-X"])
    assert args.project == "PROJ-X"


def test_record_get_file_and_project_mutually_exclusive():
    with pytest.raises(SystemExit):
        _parse(["record", "get", "x", "--file", "a.yaml", "--project", "PROJ-X"])


def test_record_set_requires_field_and_value():
    with pytest.raises(SystemExit):
        _parse(["record", "set", "field-only"])


def test_record_set_full():
    args = _parse(["record", "set", "deadline", "2026-06-01"])
    assert args.field == "deadline"
    assert args.value == "2026-06-01"


def test_record_phase_valid_choice():
    args = _parse(["record", "phase", "production"])
    assert args.phase == "production"


def test_record_phase_rejects_unknown():
    with pytest.raises(SystemExit):
        _parse(["record", "phase", "bogus"])


def test_record_sync_time():
    args = _parse(["record", "sync-time", "--file", "x.yaml"])
    assert args.cmd == "sync-time"


def test_record_copalvx_update_requires_project_name_and_version():
    """Both --project-name and --version are required.

    This is the exact shape `pm_hooks.hook_post_push` invokes from CopalVX.
    If the flags ever change, both ends need to update together.
    """
    # Missing both
    with pytest.raises(SystemExit):
        _parse(["record", "copalvx-update"])
    # Missing version
    with pytest.raises(SystemExit):
        _parse(["record", "copalvx-update", "--project-name", "X"])
    # Missing project-name
    with pytest.raises(SystemExit):
        _parse(["record", "copalvx-update", "--version", "v1.0"])


def test_record_copalvx_update_full():
    args = _parse([
        "record", "copalvx-update",
        "--project-name", "MyProj",
        "--version", "v1.3",
        "--file", "C:/p/project.yaml",
    ])
    assert args.project_name == "MyProj"
    assert args.version == "v1.3"


# ── `time` group ──────────────────────────────────────────────────────────────

def test_time_start_no_desc():
    args = _parse(["time", "start"])
    assert args.description is None


def test_time_start_with_desc_and_tool():
    args = _parse(["time", "start", "storyboard", "--tool", "aftereffects"])
    assert args.description == "storyboard"
    assert args.tool == "aftereffects"


def test_time_stop():
    args = _parse(["time", "stop"])
    assert args.cmd == "stop"


def test_time_status():
    args = _parse(["time", "status"])
    assert args.cmd == "status"


def test_time_log_requires_duration_and_desc():
    with pytest.raises(SystemExit):
        _parse(["time", "log"])
    with pytest.raises(SystemExit):
        _parse(["time", "log", "45"])  # missing description


def test_time_log_duration_is_int():
    with pytest.raises(SystemExit):
        _parse(["time", "log", "not-an-int", "desc"])


def test_time_log_full():
    args = _parse(["time", "log", "45", "client call", "--tool", "cli"])
    assert args.duration_min == 45
    assert args.description == "client call"
    assert args.tool == "cli"


# ── `service` group ───────────────────────────────────────────────────────────

def test_service_install():
    args = _parse(["service", "install"])
    assert args.group == "service" and args.cmd == "install"


def test_service_uninstall():
    args = _parse(["service", "uninstall"])
    assert args.cmd == "uninstall"


def test_service_status():
    args = _parse(["service", "status"])
    assert args.cmd == "status"


# ── `setup` / `teardown` umbrella commands ────────────────────────────────────

def test_setup_default():
    args = _parse(["setup"])
    assert args.group == "setup"
    assert args.service_only is False
    assert args.shell_only is False
    assert args.skip_service is False
    assert args.skip_shell is False


def test_setup_service_only():
    args = _parse(["setup", "--service-only"])
    assert args.service_only is True
    assert args.shell_only is False


def test_setup_shell_only():
    args = _parse(["setup", "--shell-only"])
    assert args.shell_only is True
    assert args.service_only is False


def test_setup_service_and_shell_only_mutually_exclusive():
    with pytest.raises(SystemExit):
        _parse(["setup", "--service-only", "--shell-only"])


def test_setup_skip_flags():
    args = _parse(["setup", "--skip-service"])
    assert args.skip_service is True
    args = _parse(["setup", "--skip-shell"])
    assert args.skip_shell is True


def test_teardown_default():
    args = _parse(["teardown"])
    assert args.group == "teardown"


def test_teardown_with_skip_flags():
    args = _parse(["teardown", "--skip-shell"])
    assert args.skip_shell is True


# ── `shell-integration` group ─────────────────────────────────────────────────

def test_shell_integration_install():
    args = _parse(["shell-integration", "install"])
    assert args.group == "shell-integration" and args.cmd == "install"


def test_shell_integration_uninstall():
    args = _parse(["shell-integration", "uninstall"])
    assert args.cmd == "uninstall"


def test_shell_integration_status():
    args = _parse(["shell-integration", "status"])
    assert args.cmd == "status"


def test_shell_integration_requires_subcommand():
    with pytest.raises(SystemExit):
        _parse(["shell-integration"])


# ── `shell-trigger` (hidden) group ────────────────────────────────────────────

def test_shell_trigger_start_parseable():
    args = _parse(["shell-trigger", "start", "--folder", "C:/projects/Foo"])
    assert args.group == "shell-trigger"
    assert args.trigger == "start"
    assert args.folder == "C:/projects/Foo"


def test_shell_trigger_stop_parseable():
    args = _parse(["shell-trigger", "stop", "--folder", "/tmp/p"])
    assert args.trigger == "stop"


def test_shell_trigger_new_project_parseable():
    args = _parse(["shell-trigger", "new-project", "--folder", "/tmp/p"])
    assert args.trigger == "new-project"


def test_shell_trigger_rejects_unknown_trigger():
    with pytest.raises(SystemExit):
        _parse(["shell-trigger", "bogus", "--folder", "/tmp"])


def test_shell_trigger_requires_folder():
    with pytest.raises(SystemExit):
        _parse(["shell-trigger", "start"])


def test_shell_trigger_hidden_from_help(capsys):
    """`shell-trigger` is an internal verb handler and should not appear in --help."""
    with pytest.raises(SystemExit):
        _parse(["--help"])
    out = capsys.readouterr().out
    assert "shell-trigger" not in out, (
        "shell-trigger subcommand is leaking into --help output."
    )


# ── `tui` group with new deep-link flags ─────────────────────────────────────

def test_tui_default_no_screen():
    args = _parse(["tui"])
    assert args.group == "tui"
    assert args.screen is None
    assert args.dir is None


def test_tui_screen_init_with_dir():
    args = _parse(["tui", "--screen", "init", "--dir", "C:/projects/Foo"])
    assert args.screen == "init"
    assert args.dir == "C:/projects/Foo"


def test_tui_screen_rejects_unknown():
    with pytest.raises(SystemExit):
        _parse(["tui", "--screen", "dashboard"])


# ── `deliver` group ───────────────────────────────────────────────────────────

def test_deliver_requires_path():
    with pytest.raises(SystemExit):
        _parse(["deliver"])


def test_deliver_minimal():
    args = _parse(["deliver", "Final.mp4"])
    assert args.path == "Final.mp4"
    assert args.final is False
    assert args.to == "client"


def test_deliver_full():
    args = _parse([
        "deliver", "Final.mp4",
        "--final", "--to", "broadcast",
        "--name", "Custom",
        "--note", "color-corrected",
    ])
    assert args.final is True
    assert args.to == "broadcast"
    assert args.name == "Custom"
    assert args.note == "color-corrected"


# ── Help-text invariants ──────────────────────────────────────────────────────

def test_task_tracker_hidden_from_help(capsys):
    """`task-tracker` is a daemon entry point and should not appear in --help."""
    with pytest.raises(SystemExit):
        _parse(["--help"])
    out = capsys.readouterr().out
    # The literal command "task-tracker" should not appear in user-facing help.
    assert "task-tracker" not in out, (
        "task-tracker subcommand is leaking into --help output. "
        "The _choices_actions filter in cli._build_parser() may have regressed."
    )


def test_help_does_not_show_suppress_placeholder(capsys):
    """argparse's =='SUPPRESS' literal must not leak into help output."""
    with pytest.raises(SystemExit):
        _parse(["--help"])
    out = capsys.readouterr().out
    assert "SUPPRESS" not in out
