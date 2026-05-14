# src/copalpm/project_record.py
# `copalpm record` — read, write, and query project.yaml records.
#
# Usage (from anywhere inside a project folder):
#   copalpm record show
#   copalpm record get financial.quoted_budget
#   copalpm record set deadline 2026-06-01
#   copalpm record phase production
#   copalpm record validate
#   copalpm record sync-time
#   copalpm record copalvx-update --project-name NAME --version TAG
#
# Argparse setup lives in cli.py; cmd_* handlers below take an argparse Namespace.

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import yaml

from copalpm.config import SESSIONS_LOG, DATA_DIR
from copalpm.pm import load_registry
from copalpm.project_doctor import find_orphan_sessions

# ── Constants ──────────────────────────────────────────────────────────────────

VALID_TYPES      = {"personal", "tlc", "client"}
VALID_CATEGORIES = {"tvc", "digital-signage", "b2b", "digital"}
VALID_PHASES     = ["concept", "production", "delivery", "archive"]
PHASE_ORDER      = {p: i for i, p in enumerate(VALID_PHASES)}

# Fields that may not be written via `copalpm record set` (use dedicated commands instead)
READONLY_FIELDS  = {"id", "slug", "created_at", "phase_log", "time_entries",
                    "deliverables", "copalvx", "schema_version"}


# ── YAML I/O ───────────────────────────────────────────────────────────────────

_YAML_HEADER = (
    "# project.yaml — Project Record v1\n"
    "# Reference: schema/project-record.yaml\n\n"
)


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, record: dict):
    content = _YAML_HEADER + yaml.dump(
        record,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    path.write_text(content, encoding="utf-8")


# ── Project detection ──────────────────────────────────────────────────────────

def find_project_yaml(start: Path | None = None) -> Path:
    """
    Walk up from `start` (defaults to CWD) looking for project.yaml.
    Raises SystemExit if not found.
    """
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / "project.yaml"
        if candidate.exists():
            return candidate
    print("error: no project.yaml found in this directory or any parent.", file=sys.stderr)
    print("tip:   run from inside a project folder, or use --file <path>", file=sys.stderr)
    sys.exit(1)


def resolve_project(args) -> tuple[Path, dict]:
    """Return (yaml_path, record) based on --file or --project flags, or CWD walk."""
    if hasattr(args, "file") and args.file:
        yaml_path = Path(args.file)
        if not yaml_path.exists():
            print(f"error: file not found: {yaml_path}", file=sys.stderr)
            sys.exit(1)
    elif hasattr(args, "project") and args.project:
        yaml_path = _find_by_id(args.project)
    else:
        yaml_path = find_project_yaml()
    return yaml_path, load_yaml(yaml_path)


def _find_by_id(project_id: str) -> Path:
    """Look up a project path from the registry by id."""
    from copalpm.config import REGISTRY
    if not REGISTRY.exists():
        print("error: registry not found. Run `copalpm project init` to create a project.", file=sys.stderr)
        sys.exit(1)
    items = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for item in items:
        if item.get("id") == project_id:
            return Path(item["path"]) / "project.yaml"
    print(f"error: project '{project_id}' not found in registry.", file=sys.stderr)
    sys.exit(1)


# ── Derived fields ─────────────────────────────────────────────────────────────

def current_phase(record: dict) -> str | None:
    log = record.get("phase_log") or []
    return log[-1]["phase"] if log else None


def phase_entered_at(record: dict) -> datetime | None:
    log = record.get("phase_log") or []
    if not log:
        return None
    raw = log[-1].get("entered_at")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def total_seconds(record: dict) -> int:
    return sum(int(e.get("duration_sec", 0)) for e in record.get("time_entries", []))


def seconds_by_phase(record: dict) -> dict[str, int]:
    totals: dict[str, int] = {}
    for e in record.get("time_entries", []):
        phase = e.get("phase") or "unknown"
        totals[phase] = totals.get(phase, 0) + int(e.get("duration_sec", 0))
    return totals


def fmt_duration(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def days_until(date_str: str | None) -> str:
    if not date_str:
        return "—"
    try:
        deadline = datetime.strptime(str(date_str), "%Y-%m-%d").date()
        delta    = (deadline - datetime.now().date()).days
        if delta < 0:
            return f"{abs(delta)} days overdue"
        elif delta == 0:
            return "today"
        else:
            return f"{delta} days"
    except Exception:
        return str(date_str)


# ── Dotted field access ────────────────────────────────────────────────────────

def get_field(record: dict, dotted: str):
    """Read a value from a dotted path, e.g. 'financial.quoted_budget'."""
    parts = dotted.split(".")
    node  = record
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_field(record: dict, dotted: str, raw_value: str) -> dict:
    """Write a coerced value to a dotted path. Returns updated record."""
    parts = dotted.split(".")
    node  = record
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]

    # Coerce value
    key = parts[-1]
    if raw_value.lower() == "null":
        node[key] = None
    elif raw_value.lower() == "true":
        node[key] = True
    elif raw_value.lower() == "false":
        node[key] = False
    else:
        try:
            node[key] = int(raw_value)
        except ValueError:
            try:
                node[key] = float(raw_value)
            except ValueError:
                node[key] = raw_value  # plain string

    return record


# ── Commands ───────────────────────────────────────────────────────────────────

def _active_session_for(project_id: str) -> dict | None:
    """Return the active task-tracker session if it belongs to this project. Non-fatal."""
    try:
        cfg_path = DATA_DIR / "config.json"
        if not cfg_path.exists():
            return None
        cfg  = json.loads(cfg_path.read_text(encoding="utf-8"))
        port = cfg.get("port", 5123)
        req  = urllib.request.Request(
            f"http://127.0.0.1:{port}/state",
            headers={"X-API-Key": cfg["api_key"]},
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            state = json.loads(r.read())
        if state and state.get("project_id") == project_id:
            return state
    except Exception:
        pass
    return None


def cmd_show(args):
    yaml_path, record = resolve_project(args)

    name     = record.get("name", "Untitled")
    sep      = "─" * max(0, 54 - len(name))
    print(f"\n── {name} {sep}\n")

    proj_id  = record.get("id", "—")
    proj_type = record.get("type", "—")
    category  = record.get("category", "—")
    print(f"  ID       : {proj_id}")
    print(f"  Type     : {proj_type}  /  {category}")

    # Phase
    phase = current_phase(record)
    entered = phase_entered_at(record)
    if phase and entered:
        days_in = (datetime.now(timezone.utc) - entered).days
        print(f"  Phase    : {phase}  (entered {entered.strftime('%Y-%m-%d')}, {days_in}d ago)")
    else:
        print(f"  Phase    : —")

    # Active session (non-fatal — skipped silently if service is down)
    active = _active_session_for(proj_id)
    if active:
        elapsed_s = int((datetime.now(timezone.utc) -
                         datetime.fromisoformat(active["start"].replace("Z", "+00:00"))).total_seconds())
        desc_str = f"  {active['description']}" if active.get("description") else ""
        print(f"  Active   : ⏱ {fmt_duration(elapsed_s)}{desc_str}")

    # Deadline
    deadline = record.get("deadline")
    print(f"  Deadline : {deadline or '—'}  ({days_until(deadline)})")

    # Client + people
    client   = record.get("client") or {}
    people   = record.get("people") or {}
    if client.get("name"):
        contact = f"  ({client['contact']})" if client.get("contact") else ""
        print(f"  Client   : {client['name']}{contact}")
    director = people.get("director")
    producer = people.get("producer")
    collabs  = people.get("collaborators") or []
    if director: print(f"  Director : {director}")
    if producer: print(f"  Producer : {producer}")
    if collabs:
        names = ", ".join(c.get("name", "?") for c in collabs)
        print(f"  Collab   : {names}")

    # Financial
    fin = record.get("financial") or {}
    cur = fin.get("currency", "EUR")
    if any(fin.get(k) is not None for k in ("quoted_budget", "rate_per_hour", "invoiced_amount")):
        print(f"\n── Financial {'─' * 40}\n")
        if fin.get("quoted_budget") is not None:
            print(f"  Quoted   : {cur} {fin['quoted_budget']:,.2f}")
        if fin.get("rate_per_hour") is not None:
            est = fin.get("estimated_hours")
            est_str = f"  (est. {est}h)" if est else ""
            print(f"  Rate     : {cur} {fin['rate_per_hour']:.2f}/hr{est_str}")
        if fin.get("invoiced_amount") is not None:
            paid = "✓ paid" if fin.get("paid") else "unpaid"
            print(f"  Invoiced : {cur} {fin['invoiced_amount']:,.2f}  [{paid}]")
        else:
            print(f"  Invoiced : —")

    # Time
    total_sec  = total_seconds(record)
    by_phase   = seconds_by_phase(record)
    n_entries  = len(record.get("time_entries", []))
    print(f"\n── Time {'─' * 45}\n")
    print(f"  Total    : {fmt_duration(total_sec)}  ({n_entries} session(s))")
    for ph, secs in by_phase.items():
        print(f"  {ph:<10}: {fmt_duration(secs)}")

    # CopalVX
    cvx = record.get("copalvx") or {}
    if cvx.get("last_push") or cvx.get("project_name"):
        print(f"\n── CopalVX {'─' * 43}\n")
        if cvx.get("project_name"):
            print(f"  Project  : {cvx['project_name']}")
        if cvx.get("last_push_version"):
            last_push_date = str(cvx.get("last_push", ""))[:10]
            print(f"  Last push: {cvx['last_push_version']}  ({last_push_date})")

    # Deliverables
    delivs = record.get("deliverables") or []
    print(f"\n── Deliverables {'─' * 37}\n")
    if delivs:
        last = delivs[-1]
        d_type  = last.get("type", "?")
        d_recip = last.get("recipient", "")
        d_when  = str(last.get("delivered_at", ""))[:10]
        recip_str = f" -> {d_recip}" if d_recip else ""
        print(f"  {last.get('name', '?')}  [{d_type}{recip_str}]  {d_when}")
    else:
        print(f"  No deliverables logged.")

    # Tags / notes
    tags  = record.get("tags") or []
    notes = record.get("notes") or ""
    print(f"\n── Meta {'─' * 45}\n")
    print(f"  Tags  : {', '.join(tags) if tags else '—'}")
    if notes:
        print(f"  Notes : {notes}")
    print()


def cmd_get(args):
    _, record = resolve_project(args)
    value = get_field(record, args.field)
    if value is None:
        print("null")
    elif isinstance(value, (dict, list)):
        print(yaml.dump(value, default_flow_style=False, allow_unicode=True, sort_keys=False).strip())
    else:
        print(value)


def cmd_set(args):
    yaml_path, record = resolve_project(args)

    # Block readonly fields
    top_key = args.field.split(".")[0]
    if top_key in READONLY_FIELDS:
        print(f"error: '{top_key}' cannot be set directly.", file=sys.stderr)
        if top_key == "phase" or top_key == "phase_log":
            print("       Use `copalpm record phase <name>` to log a phase transition.",
                  file=sys.stderr)
        elif top_key == "time_entries":
            print("       Use `copalpm record sync-time` to import sessions from the tracker.",
                  file=sys.stderr)
        elif top_key == "copalvx":
            print("       The copalvx block is written by `copalpm record copalvx-update`",
                  file=sys.stderr)
            print("       (called automatically by the CopalVX push hook).", file=sys.stderr)
        else:
            print(f"       '{top_key}' is immutable by design "
                  "(id, slug, created_at, schema_version, deliverables).",
                  file=sys.stderr)
        sys.exit(1)

    old_value = get_field(record, args.field)
    record    = set_field(record, args.field, args.value)
    save_yaml(yaml_path, record)

    new_value = get_field(record, args.field)
    print(f"set {args.field}: {old_value!r} → {new_value!r}")


def cmd_phase(args):
    yaml_path, record = resolve_project(args)

    new_phase = args.phase.lower()
    if new_phase not in PHASE_ORDER:
        print(f"error: invalid phase '{new_phase}'. Valid: {', '.join(VALID_PHASES)}", file=sys.stderr)
        sys.exit(1)

    current = current_phase(record)
    if current == new_phase:
        print(f"Already in phase: {new_phase}")
        return

    # Warn on backward transition
    if current and PHASE_ORDER.get(new_phase, 0) < PHASE_ORDER.get(current, 0):
        print(f"warning: transitioning backward from '{current}' to '{new_phase}'.")

    now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record.setdefault("phase_log", []).append({
        "phase":      new_phase,
        "entered_at": now_str,
    })
    save_yaml(yaml_path, record)
    print(f"Phase: {current or '—'} → {new_phase}  ({now_str[:10]})")


def cmd_validate(args):
    _, record = resolve_project(args)
    errors   = []
    warnings = []

    # Required identity fields
    for field in ("id", "name", "type", "category", "created_at"):
        if not record.get(field):
            errors.append(f"missing required field: {field}")

    # Enum checks
    if record.get("type") and record["type"] not in VALID_TYPES:
        errors.append(f"invalid type '{record['type']}'. Must be: {', '.join(VALID_TYPES)}")
    if record.get("category") and record["category"] not in VALID_CATEGORIES:
        errors.append(f"invalid category '{record['category']}'. Must be: {', '.join(VALID_CATEGORIES)}")

    # Phase log
    phase_log = record.get("phase_log") or []
    if not phase_log:
        errors.append("phase_log is empty — must have at least one entry")
    for i, entry in enumerate(phase_log):
        if entry.get("phase") not in PHASE_ORDER:
            errors.append(f"phase_log[{i}]: invalid phase '{entry.get('phase')}'")
        if not entry.get("entered_at"):
            errors.append(f"phase_log[{i}]: missing entered_at")

    # Deadline format
    deadline = record.get("deadline")
    if deadline:
        try:
            datetime.strptime(str(deadline), "%Y-%m-%d")
        except ValueError:
            errors.append(f"deadline '{deadline}' is not a valid YYYY-MM-DD date")

    # Financial types
    fin = record.get("financial") or {}
    for field in ("quoted_budget", "rate_per_hour", "estimated_hours", "invoiced_amount"):
        val = fin.get(field)
        if val is not None and not isinstance(val, (int, float)):
            errors.append(f"financial.{field} must be a number, got: {val!r}")

    # Time entries
    for i, entry in enumerate(record.get("time_entries", [])):
        if not entry.get("session_id"):
            warnings.append(f"time_entries[{i}]: missing session_id")
        if not entry.get("started_at"):
            warnings.append(f"time_entries[{i}]: missing started_at")

    # CopalVX (optional, no errors)
    if record.get("schema_version") != 1:
        warnings.append(f"schema_version is {record.get('schema_version')!r}, expected 1")

    # Report
    if errors:
        print(f"✗ {len(errors)} error(s):")
        for e in errors:
            print(f"    {e}")
    if warnings:
        print(f"△ {len(warnings)} warning(s):")
        for w in warnings:
            print(f"    {w}")
    if not errors and not warnings:
        print("✓ valid")
    if errors:
        sys.exit(1)


def cmd_sync_time(args):
    yaml_path, record = resolve_project(args)
    project_id = record.get("id")
    if not project_id:
        print("error: project.yaml has no id field.", file=sys.stderr)
        sys.exit(1)

    if not SESSIONS_LOG.exists():
        print("No sessions log found. Start tracking time with `copalpm time start`.")
        return

    # Surface orphan sessions (project_ids in the log but not in the registry).
    # Informational only — we keep going and finish the sync for `project_id`.
    orphans = find_orphan_sessions(load_registry(), SESSIONS_LOG)
    for orphan_pid, count in sorted(orphans.items()):
        plural = "session" if count == 1 else "sessions"
        print(
            f"warning: {count} {plural} in sessions.jsonl reference unregistered "
            f"project {orphan_pid} — run `copalpm project doctor` for details.",
            file=sys.stderr,
        )

    # Build set of existing session_ids for deduplication
    existing_ids = {
        e["session_id"]
        for e in record.get("time_entries", [])
        if e.get("session_id")
    }

    new_entries = []
    with SESSIONS_LOG.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                session = json.loads(line.strip())
                if session.get("project_id") != project_id:
                    continue
                sid = session.get("session_id")
                if not sid or sid in existing_ids:
                    continue
                new_entries.append({
                    "session_id":   sid,
                    "started_at":   session.get("started_at") or session.get("start"),
                    "ended_at":     session.get("ended_at")   or session.get("end"),
                    "duration_sec": int(session.get("duration_sec", 0)),
                    "phase":        session.get("phase"),
                    "description":  session.get("description") or session.get("task"),
                    "tool":         session.get("tool"),
                    "stop_reason":  session.get("stop_reason"),
                })
            except Exception:
                pass

    if not new_entries:
        print(f"Nothing to sync. {len(existing_ids)} existing session(s) already in record.")
        return

    record.setdefault("time_entries", []).extend(new_entries)
    save_yaml(yaml_path, record)
    total = len(record["time_entries"])
    print(f"Synced {len(new_entries)} new session(s). Total in record: {total}")


def cmd_copalvx_update(args):
    # Write CopalVX push metadata into the copalvx block of project.yaml.
    # Called by the CopalVX post-push hook — not intended for manual use.
    # The copalvx block is readonly to `copalpm record set` deliberately; this command
    # is the only authorised writer.
    yaml_path, record = resolve_project(args)

    # Ensure the copalvx key exists before updating sub-fields
    record.setdefault("copalvx", {})
    record["copalvx"]["project_name"]       = args.project_name
    record["copalvx"]["last_push"]          = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record["copalvx"]["last_push_version"]  = args.version

    save_yaml(yaml_path, record)
    print(f"copalvx block updated: {args.project_name} @ {args.version}")


# ── argparse helper (imported by cli.py) ──────────────────────────────────────

def _add_location_args(parser):
    """Add --file and --project flags to any subcommand."""
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--file",    metavar="PATH",  help="Explicit path to project.yaml")
    grp.add_argument("--project", metavar="ID",    help="Project ID to look up in registry")
