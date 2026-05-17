# cli.py — unified entry point for the `copalpm` command.
#
# All argparse setup lives here. Subcommand handlers live in their own modules
# (pm.py, project_record.py, time_cli.py, deliver_cli.py, task_tracker.py,
# tui_app.py) and expose `cmd_*` functions that accept an argparse Namespace.
#
# Top-level subcommand groups:
#   tui                — launch the TUI (also the default when no args given)
#   setup              — one-shot install of service + shell integration
#   teardown           — reverse of setup (remove service + shell verbs)
#   project            — registry operations (init/list/status/register/scan/remove/rollup)
#   record             — project.yaml operations (show/get/set/phase/validate/sync-time/copalvx-update)
#   time               — time tracking (start/stop/status/log)
#   service            — task-tracker background service (install/uninstall/status)
#   shell-integration  — Explorer / Finder right-click verbs (install/uninstall/status)
#   deliver            — log a delivered asset
#   whose              — given a path, report which registered project owns it
#   task-tracker       — daemon entry point (hidden; invoked by the OS service)
#   shell-trigger      — handler invoked by the OS shell verbs (hidden)

import argparse
import sys
from pathlib import Path

from .pm import (
    cmd_init,
    cmd_list,
    cmd_register,
    cmd_scan,
    cmd_remove,
    cmd_rollup,
    cmd_install_service,
    cmd_uninstall_service,
    cmd_service_status,
)
from .pm import cmd_status as cmd_project_status
from .project_record import (
    cmd_show,
    cmd_get,
    cmd_set,
    cmd_phase,
    cmd_validate,
    cmd_sync_time,
    cmd_copalvx_update,
    _add_location_args,
    VALID_PHASES,
)
from .time_cli import (
    cmd_start,
    cmd_stop,
    cmd_log,
)
from .time_cli import cmd_status as cmd_time_status
from .deliver_cli import cmd_deliver
from .task_tracker import main as run_task_tracker
from .tui_app import main as run_tui
from .shell_integration import (
    cmd_shell_install,
    cmd_shell_uninstall,
    cmd_shell_status,
    cmd_shell_trigger,
)
from .setup_cmd import cmd_setup, cmd_teardown
from .project_lookup import cmd_whose
from .template_cli import (
    cmd_template_list,
    cmd_template_export,
    cmd_template_import,
)


# ── Parser construction ───────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="copalpm",
        description="CopalPM — project management and time tracking for motion design and VFX work.",
    )
    groups = ap.add_subparsers(dest="group", metavar="<group>")

    # tui ───────────────────────────────────────────────────────────────────
    p_tui = groups.add_parser("tui", help="Launch the TUI dashboard (default)")
    p_tui.add_argument("--screen", choices=["init"], default=None,
                       help="Open the TUI directly on a specific screen")
    p_tui.add_argument("--dir", metavar="PATH", default=None,
                       help="With --screen init, pre-fill the project folder")

    # setup / teardown ──────────────────────────────────────────────────────
    p_setup = groups.add_parser(
        "setup",
        help="One-shot install of the background service and shell integration",
    )
    setup_skip = p_setup.add_mutually_exclusive_group()
    setup_skip.add_argument("--service-only", action="store_true",
                            help="Install only the background service")
    setup_skip.add_argument("--shell-only", action="store_true",
                            help="Install only the right-click shell integration")
    p_setup.add_argument("--skip-service", action="store_true",
                         help="Skip the background-service step")
    p_setup.add_argument("--skip-shell", action="store_true",
                         help="Skip the shell-integration step")

    p_teardown = groups.add_parser(
        "teardown",
        help="Reverse of setup (remove service and shell integration)",
    )
    teardown_skip = p_teardown.add_mutually_exclusive_group()
    teardown_skip.add_argument("--service-only", action="store_true",
                               help="Remove only the background service")
    teardown_skip.add_argument("--shell-only", action="store_true",
                               help="Remove only the right-click shell integration")
    p_teardown.add_argument("--skip-service", action="store_true",
                            help="Skip the background-service step")
    p_teardown.add_argument("--skip-shell", action="store_true",
                            help="Skip the shell-integration step")

    # project ────────────────────────────────────────────────────────────────
    p_proj = groups.add_parser("project", help="Project registry operations")
    s_proj = p_proj.add_subparsers(dest="cmd", required=True, metavar="<cmd>")

    p_init = s_proj.add_parser("init", help="Create a new project interactively")
    p_init.add_argument("name", help="Project title")
    p_init.add_argument("--dir", help="Base directory (defaults to CWD)")
    p_init.add_argument("--inc", action="store_true",
                        help="Append auto-incremented _NNN suffix")

    s_proj.add_parser("list", help="List registered projects")
    p_stat = s_proj.add_parser("status", help="Summary table of all registered projects")
    p_stat.add_argument("--json", action="store_true", help="Output as JSON")
    p_roll = s_proj.add_parser("rollup", help="Total time per project (all sources)")
    p_roll.add_argument("--json", action="store_true", help="Output as JSON")
    p_reg = s_proj.add_parser("register", help="Register an existing project folder")
    p_reg.add_argument("path", help="Path to the project folder (must contain project.yaml)")
    p_scan = s_proj.add_parser("scan", help="Scan a directory tree and register all projects found")
    p_scan.add_argument("directory", help="Root directory to scan")
    p_rm = s_proj.add_parser("remove", help="Remove a project from registry (keeps files on disk)")
    p_rm.add_argument("project_id")
    s_proj.add_parser("doctor",
                      help="Report registry / sessions drift (read-only)")

    # record ─────────────────────────────────────────────────────────────────
    p_rec = groups.add_parser("record", help="Read and write project.yaml fields")
    s_rec = p_rec.add_subparsers(dest="cmd", required=True, metavar="<cmd>")

    p_show = s_rec.add_parser("show", help="Pretty-print the project record with derived fields")
    _add_location_args(p_show)
    p_get = s_rec.add_parser("get", help="Read a field value (supports dotted paths)")
    p_get.add_argument("field", help="Field path, e.g. 'financial.quoted_budget'")
    _add_location_args(p_get)
    p_set = s_rec.add_parser("set", help="Write a field value")
    p_set.add_argument("field", help="Field path, e.g. 'deadline'")
    p_set.add_argument("value", help="Value to set (null / true / false / number / string)")
    _add_location_args(p_set)
    p_phase = s_rec.add_parser("phase", help="Log a phase transition")
    p_phase.add_argument("phase", choices=VALID_PHASES)
    _add_location_args(p_phase)
    p_val = s_rec.add_parser("validate", help="Validate the project record against the schema")
    _add_location_args(p_val)
    p_sync = s_rec.add_parser("sync-time",
                              help="Pull sessions from sessions.jsonl into time_entries (idempotent)")
    _add_location_args(p_sync)
    p_cvu = s_rec.add_parser("copalvx-update",
                             help="Write CopalVX push metadata into project.yaml (called by CopalVX hook)")
    p_cvu.add_argument("--project-name", required=True,
                       help="CopalVX project name used as the server-side identifier")
    p_cvu.add_argument("--version", required=True,
                       help="CopalVX version tag that was just pushed (e.g. v1.3)")
    _add_location_args(p_cvu)

    # time ───────────────────────────────────────────────────────────────────
    p_time = groups.add_parser("time", help="Time tracking sessions")
    s_time = p_time.add_subparsers(dest="cmd", required=True, metavar="<cmd>")

    p_start = s_time.add_parser("start", help="Start a tracking session")
    p_start.add_argument("description", nargs="?", default=None,
                         help="What you're working on, e.g. 'storyboard'")
    p_start.add_argument("--project", metavar="ID",
                         help="Project ID (defaults to CWD project)")
    p_start.add_argument("--tool", metavar="NAME",
                         help="Tool in use: aftereffects, blender, illustrator, cli, etc.")
    p_start.add_argument("--phase", metavar="PHASE",
                         help="Phase override (defaults to current phase in project.yaml)")
    s_time.add_parser("stop", help="Stop the current session")
    s_time.add_parser("status", help="Show the current active session")
    p_tlog = s_time.add_parser("log", help="Manually log time (no service required)")
    p_tlog.add_argument("duration_min", type=int, help="Duration in minutes")
    p_tlog.add_argument("description", help="What you worked on")
    p_tlog.add_argument("--tool", metavar="NAME", help="Tool used")
    p_tlog.add_argument("--phase", metavar="PHASE", help="Phase override")

    # service ────────────────────────────────────────────────────────────────
    p_svc = groups.add_parser("service", help="Task-tracker background service")
    s_svc = p_svc.add_subparsers(dest="cmd", required=True, metavar="<cmd>")
    s_svc.add_parser("install", help="Install and start the task-tracker background service")
    s_svc.add_parser("uninstall", help="Stop and remove the task-tracker service")
    s_svc.add_parser("status", help="Show service state and current open session")

    # shell-integration ──────────────────────────────────────────────────────
    p_shell = groups.add_parser(
        "shell-integration",
        help="Install Explorer / Finder right-click verbs",
    )
    s_shell = p_shell.add_subparsers(dest="cmd", required=True, metavar="<cmd>")
    s_shell.add_parser("install",
                       help="Add right-click menu shortcuts on Windows Explorer and macOS Finder")
    s_shell.add_parser("uninstall", help="Remove the Copal verbs from the OS shell")
    s_shell.add_parser("status", help="Show which Copal verbs are currently installed")

    # deliver ────────────────────────────────────────────────────────────────
    p_del = groups.add_parser("deliver", help="Log a delivered asset bundle into project.yaml")
    p_del.add_argument("path", nargs="+",
                       help="One or more paths to delivered file(s). "
                            "Multiple paths land in a single deliverable entry.")
    p_del.add_argument("--final", action="store_true",
                       help="Mark as final (default: draft)")
    p_del.add_argument("--to", metavar="RECIPIENT", default="client",
                       help="Recipient: internal | client | broadcast  (default: client)")
    p_del.add_argument("--name", metavar="NAME",
                       help="Display name (default: first filename without extension)")
    p_del.add_argument("--note", metavar="TEXT", help="Optional notes")
    p_del.add_argument("--file", metavar="PATH",
                       help="Explicit path to project.yaml (defaults to CWD walk)")

    # whose ──────────────────────────────────────────────────────────────────
    p_whose = groups.add_parser(
        "whose",
        help="Report which registered project owns a given file or folder",
    )
    p_whose.add_argument("path", help="File or folder path to look up")
    p_whose.add_argument("--json", action="store_true",
                         help="Emit a JSON object (or `null` on miss) for scripts and editor integrations")

    # template ───────────────────────────────────────────────────────────────
    p_tmpl = groups.add_parser(
        "template",
        help="Project templates: list, export, import (editing happens in the TUI)",
    )
    s_tmpl = p_tmpl.add_subparsers(dest="cmd", required=True, metavar="<cmd>")

    p_tlist = s_tmpl.add_parser("list", help="List installed templates")
    p_tlist.add_argument("--json", action="store_true",
                         help="Emit a JSON array of templates")

    p_texp = s_tmpl.add_parser("export", help="Write a template's YAML to a file")
    p_texp.add_argument("id", help="Template id (see `copalpm template list`)")
    p_texp.add_argument("--out", metavar="PATH",
                        help="Output file path (defaults to ./<id>.yaml)")

    p_timp = s_tmpl.add_parser("import", help="Validate and install a template YAML")
    p_timp.add_argument("path", help="Path to the template YAML file")
    p_timp.add_argument("--force", action="store_true",
                        help="Overwrite if a template with that id already exists")

    # task-tracker (hidden — daemon entry point invoked by the OS service) ──
    # argparse displays `help=SUPPRESS` subparsers as "==SUPPRESS==" in --help
    # output rather than hiding them. The standard workaround is to drop the
    # subparser from the subparsers action's _choices_actions list after the
    # parser tree is built. The subcommand still parses and dispatches normally.
    groups.add_parser("task-tracker", help=argparse.SUPPRESS)

    # shell-trigger (hidden — invoked by the OS shell verbs) ────────────────
    # --folder is used by the folder-targeted verbs (start, stop, new-project),
    # --file by file-targeted verbs (mark-deliverable). Exactly one is required.
    p_strg = groups.add_parser("shell-trigger", help=argparse.SUPPRESS)
    p_strg.add_argument("trigger",
                        choices=["start", "stop", "new-project", "mark-deliverable"])
    strg_target = p_strg.add_mutually_exclusive_group(required=True)
    strg_target.add_argument("--folder", metavar="PATH")
    strg_target.add_argument("--file",   metavar="PATH")

    groups._choices_actions = [
        a for a in groups._choices_actions
        if a.dest not in ("task-tracker", "shell-trigger")
    ]

    return ap


# ── Dispatch ──────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap   = _build_parser()
    args = ap.parse_args()

    # No group given → launch TUI (the most common entry point)
    if args.group is None or args.group == "tui":
        screen = getattr(args, "screen", None)
        directory = getattr(args, "dir", None)
        return run_tui(initial_screen=screen, initial_dir=directory)

    if args.group == "task-tracker":
        return run_task_tracker()

    if args.group == "deliver":
        return cmd_deliver(args)

    if args.group == "project":
        if args.cmd == "init":
            base = Path(args.dir) if args.dir else Path.cwd()
            return cmd_init(args.name, base, args.inc)
        if args.cmd == "list":
            return cmd_list()
        if args.cmd == "status":
            return cmd_project_status(as_json=args.json)
        if args.cmd == "rollup":
            return cmd_rollup(as_json=args.json)
        if args.cmd == "register":
            return cmd_register(Path(args.path))
        if args.cmd == "scan":
            return cmd_scan(Path(args.directory))
        if args.cmd == "remove":
            sys.exit(cmd_remove(args.project_id))
        if args.cmd == "doctor":
            from .project_doctor import cmd_doctor
            return cmd_doctor(args)

    if args.group == "record":
        dispatch = {
            "show":           cmd_show,
            "get":            cmd_get,
            "set":            cmd_set,
            "phase":          cmd_phase,
            "validate":       cmd_validate,
            "sync-time":      cmd_sync_time,
            "copalvx-update": cmd_copalvx_update,
        }
        return dispatch[args.cmd](args)

    if args.group == "time":
        dispatch = {
            "start":  cmd_start,
            "stop":   cmd_stop,
            "status": cmd_time_status,
            "log":    cmd_log,
        }
        return dispatch[args.cmd](args)

    if args.group == "service":
        if args.cmd == "install":
            return cmd_install_service()
        if args.cmd == "uninstall":
            return cmd_uninstall_service()
        if args.cmd == "status":
            return cmd_service_status()

    if args.group == "shell-integration":
        dispatch = {
            "install":   cmd_shell_install,
            "uninstall": cmd_shell_uninstall,
            "status":    cmd_shell_status,
        }
        return dispatch[args.cmd](args)

    if args.group == "shell-trigger":
        return cmd_shell_trigger(args)

    if args.group == "setup":
        return cmd_setup(args)

    if args.group == "teardown":
        return cmd_teardown(args)

    if args.group == "whose":
        return cmd_whose(args)

    if args.group == "template":
        dispatch = {
            "list":   cmd_template_list,
            "export": cmd_template_export,
            "import": cmd_template_import,
        }
        return dispatch[args.cmd](args)

    # Unreachable — argparse rejects unknown groups before we get here
    ap.error(f"unknown group: {args.group}")


if __name__ == "__main__":
    main()
