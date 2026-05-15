"""Smoke import tests for the copalpm package.

These catch regressions like:
- A module rename that breaks `from copalpm.X import Y` somewhere
- A handler function deleted but still referenced from cli.py
- A circular import introduced by future refactors

Fast (< 1s) and require no external state.
"""

import pytest


def test_import_package():
    import copalpm  # noqa: F401


def test_import_cli_module():
    from copalpm import cli
    assert callable(cli.main)
    assert callable(cli._build_parser)


def test_import_pm_handlers():
    from copalpm.pm import (
        cmd_init, cmd_list, cmd_status, cmd_register, cmd_scan,
        cmd_rollup, cmd_remove,
        cmd_install_service, cmd_uninstall_service, cmd_service_status,
    )
    for fn in (cmd_init, cmd_list, cmd_status, cmd_register, cmd_scan,
               cmd_rollup, cmd_remove,
               cmd_install_service, cmd_uninstall_service, cmd_service_status):
        assert callable(fn), fn


def test_import_pm_internals():
    """Private helpers used by tui_app + cli.py."""
    from copalpm.pm import (
        _YAML_HEADER, build_project_record, compute_id_and_path,
        days_ago, fmt_h, load_project_yaml, load_registry, save_registry,
        load_templates, save_templates, upsert_registry,
    )
    assert _YAML_HEADER.startswith("# project.yaml")
    assert callable(build_project_record)
    assert callable(compute_id_and_path)


def test_import_record_handlers():
    from copalpm.project_record import (
        cmd_show, cmd_get, cmd_set, cmd_phase, cmd_validate,
        cmd_sync_time, cmd_copalvx_update, _add_location_args, VALID_PHASES,
    )
    for fn in (cmd_show, cmd_get, cmd_set, cmd_phase, cmd_validate,
               cmd_sync_time, cmd_copalvx_update):
        assert callable(fn), fn
    assert callable(_add_location_args)
    assert isinstance(VALID_PHASES, list)
    assert "concept" in VALID_PHASES


def test_import_time_handlers():
    from copalpm.time_cli import cmd_start, cmd_stop, cmd_status, cmd_log
    for fn in (cmd_start, cmd_stop, cmd_status, cmd_log):
        assert callable(fn), fn


def test_import_deliver_handler():
    from copalpm.deliver_cli import cmd_deliver
    assert callable(cmd_deliver)


def test_import_task_tracker_main():
    """The daemon entry point used by `copalpm task-tracker`."""
    from copalpm.task_tracker import main
    assert callable(main)


def test_import_tui_main():
    """The TUI entry point used by `copalpm tui`."""
    from copalpm.tui_app import main
    assert callable(main)


def test_import_config():
    from copalpm.config import DATA_DIR, REGISTRY, SESSIONS_LOG, TEMPLATES_FILE
    assert DATA_DIR.name == "copalpm"


def test_import_copalvx_api():
    """Module that handles the outbound subprocess calls to `copalvx`."""
    from copalpm import copalvx_api
    assert hasattr(copalvx_api, "run_push")
    assert hasattr(copalvx_api, "run_pull")


def test_no_lingering_old_package_name():
    """No file in the installed copalpm should reference `project_registry`."""
    import copalpm
    from pathlib import Path
    pkg_dir = Path(copalpm.__file__).parent
    offenders = []
    for py in pkg_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        # Imports of the old package name
        if "from project_registry" in text or "import project_registry" in text:
            offenders.append(str(py))
    assert offenders == [], f"old import paths still present: {offenders}"
