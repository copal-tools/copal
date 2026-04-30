# src/project_registry/pm.py
import json, os, re, sys, platform, argparse
from datetime import datetime, timezone
from pathlib import Path

import yaml

from project_registry.config import DATA_DIR, REGISTRY, SESSIONS_LOG


DATA = DATA_DIR
DATA.mkdir(parents=True, exist_ok=True)
REG  = REGISTRY
SESS = SESSIONS_LOG


# ── Helpers ────────────────────────────────────────────────────────────────────

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slug_title(title: str) -> str:
    """UPPERCASE slug for project ID: spaces → hyphens, strip non-alphanumeric."""
    s = title.strip().replace(" ", "-")
    s = re.sub(r"[^A-Za-z0-9\-_]+", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.upper()


def make_slug(title: str) -> str:
    """Lowercase slug for project.yaml slug field: spaces → hyphens."""
    s = title.strip().lower().replace(" ", "-")
    s = re.sub(r"[^a-z0-9\-]+", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def next_suffix_from_dir(base_dir: Path) -> int:
    """Scan sibling dirs for trailing 3-digit suffixes; return max + 1."""
    max_seen = 0
    if not base_dir.exists():
        return 1
    for p in base_dir.iterdir():
        if p.is_dir() and len(p.name) >= 3 and p.name[-3:].isdigit():
            n = int(p.name[-3:])
            if n > max_seen:
                max_seen = n
    return max_seen + 1


def compute_id_and_path(title: str, base_dir: Path, use_increment: bool) -> tuple[str, Path]:
    tslug     = slug_title(title)
    date_part = datetime.now().strftime("%d%m%y")
    core      = f"{tslug}-{date_part}"
    if use_increment:
        folder_name = f"{core}_{next_suffix_from_dir(base_dir):03d}"
    else:
        folder_name = core
    return f"PROJ-{folder_name}", base_dir / folder_name


# ── Registry (upsert-by-id) ────────────────────────────────────────────────────

def load_registry() -> list:
    if REG.exists():
        return json.loads(REG.read_text(encoding="utf-8"))
    return []


def save_registry(items: list):
    REG.write_text(json.dumps(items, indent=2), encoding="utf-8")


def upsert_registry(pid: str, name: str, path: Path):
    """Insert or update registry entry by id. Updates path if project moved."""
    items = load_registry()
    for item in items:
        if item.get("id") == pid:
            item["path"] = str(path)
            save_registry(items)
            return
    items.append({"id": pid, "name": name, "path": str(path), "registered_at": iso_now()})
    save_registry(items)


# ── Interactive prompts ────────────────────────────────────────────────────────

def ask(prompt: str, default=None, required=False) -> str | None:
    """Prompt the user for input. Returns None if skipped and not required."""
    hint = f" [{default}]" if default else (" [enter to skip]" if not required else "")
    while True:
        val = input(f"  {prompt}{hint}: ").strip()
        if val:
            return val
        if default:
            return default
        if not required:
            return None
        print("    This field is required.")


def ask_choice(prompt: str, choices: list[str], default: str) -> str:
    """Prompt for a choice from a fixed list."""
    options = "/".join(choices)
    while True:
        val = input(f"  {prompt} ({options}) [{default}]: ").strip().lower()
        if not val:
            return default
        if val in choices:
            return val
        print(f"    Please enter one of: {options}")


# ── Quick-init presets ─────────────────────────────────────────────────────────

QUICK_PRESETS = {
    "tactical": {
        "type":          "tlc",
        "category":      "tvc",
        "client":        "Public",
        "director":      "",
        "producer":      "",
        "collaborators": [{"name": "", "role": "audio"}],
    },
    "ds": {
        "type":          "tlc",
        "category":      "digital-signage",
        "client":        "Public",
        "director":      None,
        "producer":      None,
        "collaborators": [],
    },
}

_YAML_HEADER = (
    "# project.yaml — Project Record v1\n"
    "# Reference: schema/project-record.yaml\n\n"
)


# ── project.yaml builder ───────────────────────────────────────────────────────

def build_project_record(pid: str, name: str, proj_type: str, category: str,
                          client_name, client_contact, director, producer,
                          deadline, budget, rate, est_hours,
                          collaborators=None) -> dict:
    return {
        "schema_version": 1,

        # Identity
        "id":       pid,
        "name":     name,
        "slug":     make_slug(name),
        "type":     proj_type,
        "category": category,

        # People
        "client": {
            "name":    client_name,
            "contact": client_contact,
        },
        "people": {
            "director":      director,
            "producer":      producer,
            "collaborators": collaborators if collaborators is not None else [],
        },

        # Timeline
        "created_at": iso_now(),
        "deadline":   deadline,
        "phase_log": [
            {"phase": "concept", "entered_at": iso_now()}
        ],

        # Financial
        "financial": {
            "currency":        "EUR",
            "quoted_budget":   float(budget)    if budget    else None,
            "rate_per_hour":   float(rate)      if rate      else None,
            "estimated_hours": float(est_hours) if est_hours else None,
            "invoiced_amount": None,
            "invoiced_at":     None,
            "paid":            None,
            "paid_at":         None,
        },

        # Time tracking (populated by `project sync-time`)
        "time_entries": [],

        # Deliverables
        "deliverables": [],

        # CopalVX (written automatically by post-push hook)
        "copalvx": {
            "project_id":        None,
            "project_name":      None,
            "last_push":         None,
            "last_push_version": None,
        },

        # Meta
        "tags":  [],
        "notes": "",
    }


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_init(name: str, base_dir: Path, use_increment: bool, preset: str | None = None):
    pid, root = compute_id_and_path(name, base_dir, use_increment)

    if root.exists() and not use_increment:
        print(f"error: target folder already exists: {root}\n"
              f"tip:   re-run with --inc to auto-append an incremented suffix.", file=sys.stderr)
        sys.exit(1)

    print(f"\nInitialising: {name}")
    print(f"ID:           {pid}\n")

    if preset:
        p              = QUICK_PRESETS[preset]
        proj_type      = p["type"]
        category       = p["category"]
        client_name    = p["client"]
        client_contact = None
        director       = p["director"]
        producer       = p["producer"]
        collaborators  = p["collaborators"]
        deadline = budget = rate = est_hours = None
        print(f"  [{preset}]  {proj_type} / {category} / {client_name}")
    else:
        proj_type = ask_choice("Type", ["personal", "tlc", "client"], "tlc")
        category  = ask_choice(
            "Category",
            ["tvc", "digital-signage", "b2b", "digital"],
            "tvc",
        )

        client_name    = None
        client_contact = None
        if proj_type != "personal":
            client_name    = ask("Client name")
            client_contact = ask("Client contact (name or email)")

        director      = ask("Director")
        producer      = ask("Producer")
        deadline      = ask("Deadline (YYYY-MM-DD)")
        collaborators = None

        budget    = None
        rate      = None
        est_hours = None
        if proj_type != "personal":
            budget    = ask("Quoted budget (EUR)")
            rate      = ask("Rate per hour (EUR)")
            est_hours = ask("Estimated hours")

    # Create folder structure
    root.mkdir(parents=True, exist_ok=True)
    for d in ["01_Intake", "02_Workfiles", "03_Exports"]:
        (root / d).mkdir(exist_ok=True)

    # Write project.yaml
    record    = build_project_record(pid, name, proj_type, category,
                                     client_name, client_contact,
                                     director, producer,
                                     deadline, budget, rate, est_hours,
                                     collaborators=collaborators)
    yaml_path = root / "project.yaml"
    yaml_path.write_text(
        _YAML_HEADER + yaml.dump(record, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # Register
    upsert_registry(pid, name, root)

    print(f"\n  project folder : {root}")
    print(f"  project.yaml   : {yaml_path}")
    print(f"  registry       : {REG}")
    print(f"\n{pid}")
    print(f"\n  Next: cd into the project folder and run  tt start  to begin tracking.")


def cmd_list():
    items = load_registry()
    if not items:
        print("No projects registered.")
        return
    for p in items:
        print(p["id"], "—", p.get("name", ""), "—", p.get("path", ""))


def cmd_register(path: Path):
    """Upsert a project into the registry from an existing project.yaml."""
    yaml_path = path / "project.yaml"
    if not yaml_path.exists():
        print(f"error: no project.yaml found at {yaml_path}", file=sys.stderr)
        sys.exit(1)
    with yaml_path.open("r", encoding="utf-8") as f:
        record = yaml.safe_load(f)
    pid  = record.get("id")
    name = record.get("name", "")
    if not pid:
        print("error: project.yaml is missing the 'id' field.", file=sys.stderr)
        sys.exit(1)
    upsert_registry(pid, name, path)
    print(f"registered: {pid} -> {path}")


def cmd_scan(directory: Path):
    """Walk a directory tree and register all folders containing project.yaml."""
    found = 0
    for yaml_path in directory.rglob("project.yaml"):
        proj_dir = yaml_path.parent
        try:
            with yaml_path.open("r", encoding="utf-8") as f:
                record = yaml.safe_load(f)
            pid  = record.get("id")
            name = record.get("name", "")
            if pid:
                upsert_registry(pid, name, proj_dir)
                print(f"  registered: {pid} — {proj_dir}")
                found += 1
        except Exception as e:
            print(f"  skipped {yaml_path}: {e}", file=sys.stderr)
    print(f"\n{found} project(s) registered from {directory}")


def _fmt_h(seconds: int) -> str:
    return f"{seconds / 3600:.1f}h"


def _days_ago(iso_str: str) -> str:
    try:
        dt   = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - dt).days
        return "today" if days == 0 else f"{days}d ago"
    except Exception:
        return "?"


def _load_project_yaml(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _project_bin() -> str:
    """Resolve the project executable in the same venv as this process."""
    name = "project.exe" if sys.platform == "win32" else "project"
    return str(Path(sys.executable).parent / name)


def cmd_rollup(as_json: bool = False):
    """Sync all registered projects then total time from project.yaml time_entries."""
    import subprocess
    registry = load_registry()
    names    = {r["id"]: r.get("name", r["id"]) for r in registry}
    project  = _project_bin()

    totals: dict[str, int] = {}
    for entry in registry:
        pid       = entry["id"]
        yaml_path = Path(entry.get("path", "")) / "project.yaml"
        if not yaml_path.exists():
            continue
        # Sync sessions.jsonl into project.yaml first (idempotent, silent)
        subprocess.run([project, "sync-time", "--file", str(yaml_path)], capture_output=True)
        record = _load_project_yaml(yaml_path)
        sec    = sum(int(te.get("duration_sec", 0)) for te in record.get("time_entries", []))
        if sec:
            totals[pid] = sec

    if not totals:
        print("No time recorded yet.")
        return

    rows = sorted(totals.items(), key=lambda x: -x[1])

    if as_json:
        print(json.dumps([
            {"id": pid, "name": names.get(pid, pid), "hours": round(sec / 3600, 2)}
            for pid, sec in rows
        ]))
        return

    name_w = max(len("Project"), max(len(names.get(pid, pid)) for pid, _ in rows))
    print()
    print(f"  {'Project':<{name_w}}  Time")
    print("  " + "-" * (name_w + 8))
    for pid, sec in rows:
        print(f"  {names.get(pid, pid):<{name_w}}  {_fmt_h(sec)}")
    print(f"\n  Total: {_fmt_h(sum(totals.values()))}")


def cmd_status(as_json: bool = False):
    """Summary table of all registered projects."""
    registry = load_registry()
    if not registry:
        print("No projects registered. Run `pm init` or `pm register` to add one.")
        return

    rows = []
    for entry in registry:
        pid  = entry["id"]
        name = entry.get("name", pid)
        path = Path(entry.get("path", ""))
        yaml_path = path / "project.yaml"

        phase        = "missing"
        total_sec    = 0
        deadline     = None
        last_delivery = None

        if yaml_path.exists():
            record = _load_project_yaml(yaml_path)
            phase_log = record.get("phase_log") or []
            phase     = phase_log[-1].get("phase", "?") if phase_log else "?"
            total_sec = sum(int(te.get("duration_sec", 0))
                            for te in record.get("time_entries", []))
            deadline  = record.get("deadline")
            delivs    = record.get("deliverables") or []
            if delivs:
                d = delivs[-1]
                last_delivery = {
                    "name":         d.get("name", "?"),
                    "type":         d.get("type", "?"),
                    "delivered_at": d.get("delivered_at", ""),
                }

        rows.append({
            "id":            pid,
            "name":          name,
            "phase":         phase,
            "total_sec":     total_sec,
            "deadline":      str(deadline) if deadline else "—",
            "last_delivery": last_delivery,
            "path":          str(path),
        })

    if as_json:
        print(json.dumps([
            {
                "id":            r["id"],
                "name":          r["name"],
                "phase":         r["phase"],
                "total_hours":   round(r["total_sec"] / 3600, 2),
                "deadline":      None if r["deadline"] == "—" else r["deadline"],
                "last_delivery": r["last_delivery"],
                "path":          r["path"],
            }
            for r in rows
        ], indent=2, ensure_ascii=False))
        return

    name_w  = max(len("Name"),  max(len(r["name"])  for r in rows))
    phase_w = max(len("Phase"), max(len(r["phase"]) for r in rows))

    print(f"\n  {len(rows)} project(s)\n")
    header = f"  {'Name':<{name_w}}  {'Phase':<{phase_w}}  {'Time':>6}  {'Deadline':<12}  Last delivery"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for r in rows:
        time_str = _fmt_h(r["total_sec"]) if r["total_sec"] > 0 else "—"
        if r["last_delivery"]:
            d  = r["last_delivery"]
            rel = _days_ago(d["delivered_at"]) if d["delivered_at"] else "?"
            delivery_str = f"{d['name']} ({rel})"
        else:
            delivery_str = "—"
        print(
            f"  {r['name']:<{name_w}}  {r['phase']:<{phase_w}}  "
            f"{time_str:>6}  {r['deadline']:<12}  {delivery_str}"
        )

    total_all = sum(r["total_sec"] for r in rows)
    print(f"\n  Total logged: {_fmt_h(total_all)}\n")


def cmd_remove(project_id: str) -> int:
    items     = load_registry()
    new_items = [p for p in items if p.get("id") != project_id]
    if len(new_items) == len(items):
        print(f"error: project not found: {project_id}", file=sys.stderr)
        return 1
    save_registry(new_items)
    print(f"removed {project_id} from registry (files left intact).")
    return 0


# ── Service management ─────────────────────────────────────────────────────────

_PLIST_LABEL = "com.projectregistry.task-tracker"
_PLIST_PATH  = Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"


def _task_tracker_bin() -> Path:
    """Resolve the task-tracker executable (same venv as this process)."""
    bin_dir = Path(sys.executable).parent
    # Windows entry-points are installed as .exe; other platforms have no extension
    name   = "task-tracker.exe" if sys.platform == "win32" else "task-tracker"
    binary = bin_dir / name
    if not binary.exists():
        print("error: task-tracker binary not found. Run `uv sync` first.", file=sys.stderr)
        sys.exit(1)
    return binary


def cmd_install_service():
    import platform, subprocess, time as _time

    system = platform.system()

    if system == "Darwin":
        binary = _task_tracker_bin()
        DATA.mkdir(parents=True, exist_ok=True)

        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{binary}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{DATA / "service.out.log"}</string>
    <key>StandardErrorPath</key>
    <string>{DATA / "service.err.log"}</string>
</dict>
</plist>"""

        _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PLIST_PATH.write_text(plist)

        uid = os.getuid()
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(_PLIST_PATH)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Already loaded — try unloading first then re-loading
            subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(_PLIST_PATH)],
                           capture_output=True)
            subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(_PLIST_PATH)],
                           check=True, capture_output=True)

        print(f"  Service installed : {_PLIST_PATH}")
        print(f"  Auto-start        : on login")

        _time.sleep(1)  # give the service a moment to write config.json
        cfg_path = DATA / "config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            print(f"\n  API key : {cfg['api_key']}")
            print(f"  Port    : {cfg.get('port', 5123)}")
        print(f"\n  Logs    : {DATA}/service.{{out,err}}.log")

    elif system == "Windows":
        import ctypes, shutil
        # Service install requires admin — give a clear error rather than a
        # cryptic NSSM failure if the terminal isn't elevated.
        if not ctypes.windll.shell32.IsUserAnAdmin():
            print("error: installing a Windows service requires an elevated terminal.", file=sys.stderr)
            print("       Re-run this command in an Administrator PowerShell.", file=sys.stderr)
            sys.exit(1)

        binary = _task_tracker_bin()
        DATA.mkdir(parents=True, exist_ok=True)

        # Locate NSSM: prefer PATH, then the known install location.
        nssm = shutil.which("nssm") or r"C:\nssm-2.24\win64\nssm.exe"
        if not Path(nssm).exists():
            print(f"error: nssm not found. Install from https://nssm.cc and add to PATH.", file=sys.stderr)
            sys.exit(1)

        svc = "TaskTracker"
        # If a stale service entry exists from a previous install, remove it cleanly
        # before re-installing so NSSM doesn't fail with "service already exists".
        subprocess.run([nssm, "stop",   svc], capture_output=True)
        subprocess.run([nssm, "remove", svc, "confirm"], capture_output=True)

        subprocess.run([nssm, "install",   svc, str(binary)],                         check=True)
        subprocess.run([nssm, "set",       svc, "Start",     "SERVICE_AUTO_START"],    check=True)
        subprocess.run([nssm, "set",       svc, "AppStdout", str(DATA / "service.out.log")], check=True)
        subprocess.run([nssm, "set",       svc, "AppStderr", str(DATA / "service.err.log")], check=True)
        # Pin APPDATA to the installing user's directory so the service writes
        # config.json and sessions.jsonl to the same place regardless of which
        # Windows account NSSM uses to run the service (default: LocalSystem).
        user_appdata = os.environ.get("APPDATA", "")
        if user_appdata:
            subprocess.run([nssm, "set", svc, "AppEnvironmentExtra",
                            f"APPDATA={user_appdata}"], check=True)
        subprocess.run([nssm, "start",     svc],                                       check=True)

        print(f"  Service installed : '{svc}' (auto-start on login)")
        print(f"  Binary            : {binary}")

        # Give the service a moment to start and write config.json
        _time.sleep(2)
        cfg_path = DATA / "config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            print(f"\n  API key : {cfg['api_key']}")
            print(f"  Port    : {cfg.get('port', 5123)}")
        print(f"\n  Logs    : {DATA / 'service.out.log'}")
    else:
        print(f"error: unsupported platform '{system}'.", file=sys.stderr)
        sys.exit(1)


def cmd_uninstall_service():
    import platform, subprocess

    system = platform.system()

    if system == "Darwin":
        if not _PLIST_PATH.exists():
            print("Service is not installed.")
            return
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(_PLIST_PATH)],
                       capture_output=True)
        _PLIST_PATH.unlink()
        print("Service uninstalled.")

    elif system == "Windows":
        import ctypes, shutil
        if not ctypes.windll.shell32.IsUserAnAdmin():
            print("error: removing a Windows service requires an elevated terminal.", file=sys.stderr)
            print("       Re-run this command in an Administrator PowerShell.", file=sys.stderr)
            sys.exit(1)
        nssm = shutil.which("nssm") or r"C:\nssm-2.24\win64\nssm.exe"
        subprocess.run([nssm, "stop",   "TaskTracker"], capture_output=True)
        subprocess.run([nssm, "remove", "TaskTracker", "confirm"], check=True)
        print("Service uninstalled.")
    else:
        print(f"error: unsupported platform '{system}'.", file=sys.stderr)
        sys.exit(1)


def cmd_service_status():
    import urllib.request, urllib.error

    cfg_path = DATA / "config.json"
    if not cfg_path.exists():
        print("Service not configured. Run `pm install-service` first.")
        return

    cfg     = json.loads(cfg_path.read_text())
    port    = cfg.get("port", 5123)
    api_key = cfg["api_key"]

    # Liveness
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            print(f"  Service : running  (port {port})")
    except Exception:
        print(f"  Service : not running  (port {port})")
        return

    # Current session
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/state",
        headers={"X-API-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            state = json.loads(r.read())
        if state:
            started = str(state.get("start", "?"))[:19].replace("T", " ")
            desc    = state.get("description") or "—"
            print(f"  Session : {state['project_id']}")
            print(f"            {desc}  (started {started} UTC)")
        else:
            print("  Session : none")
    except Exception as e:
        print(f"  Session : error ({e})")


# ── CLI entry point ────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap  = argparse.ArgumentParser(prog="pm", description="Project Manager CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("init", help="Create a new project interactively")
    a.add_argument("name", help="Project title")
    a.add_argument("--dir", help="Base directory (defaults to CWD)")
    a.add_argument("--inc", action="store_true", help="Append auto-incremented _NNN suffix")
    quick = a.add_mutually_exclusive_group()
    quick.add_argument("--tactical", action="store_true",
                       help="Quick init: TLC Tactical (tlc/tvc/Public///, no prompts)")
    quick.add_argument("--ds", action="store_true",
                       help="Quick init: Digital Signage (tlc/digital-signage/Public, no prompts)")

    sub.add_parser("list",   help="List registered projects")
    sub.add_parser("status", help="Summary table of all registered projects").add_argument(
        "--json", action="store_true", help="Output as JSON")
    p_rollup = sub.add_parser("rollup", help="Total time per project (all sources)")
    p_rollup.add_argument("--json", action="store_true", help="Output as JSON")
    sub.add_parser("install-service",   help="Install and start the task-tracker background service")
    sub.add_parser("uninstall-service", help="Stop and remove the task-tracker service")
    sub.add_parser("service-status",    help="Show service state and current open session")

    r = sub.add_parser("register", help="Register an existing project folder")
    r.add_argument("path", help="Path to the project folder (must contain project.yaml)")

    s = sub.add_parser("scan", help="Scan a directory tree and register all projects found")
    s.add_argument("directory", help="Root directory to scan")

    rm = sub.add_parser("remove", help="Remove a project from registry (keeps files on disk)")
    rm.add_argument("project_id")

    args = ap.parse_args()

    if args.cmd == "init":
        base   = Path(args.dir) if args.dir else Path.cwd()
        preset = "tactical" if args.tactical else ("ds" if args.ds else None)
        cmd_init(args.name, base, args.inc, preset=preset)
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "status":
        cmd_status(as_json=args.json)
    elif args.cmd == "register":
        cmd_register(Path(args.path))
    elif args.cmd == "scan":
        cmd_scan(Path(args.directory))
    elif args.cmd == "rollup":
        cmd_rollup(as_json=args.json)
    elif args.cmd == "remove":
        sys.exit(cmd_remove(args.project_id))
    elif args.cmd == "install-service":
        cmd_install_service()
    elif args.cmd == "uninstall-service":
        cmd_uninstall_service()
    elif args.cmd == "service-status":
        cmd_service_status()


if __name__ == "__main__":
    main()
