# src/copalpm/time_cli.py
# `copalpm time` — time tracking CLI, a thin wrapper around the task-tracker HTTP service.
#
# Usage (from inside a project folder):
#   copalpm time start "storyboard"
#   copalpm time start "compositing" --tool aftereffects --phase production
#   copalpm time stop
#   copalpm time status
#   copalpm time log 45 "client call"      # manual entry, bypasses service
#
# The argparse setup lives in cli.py; cmd_* handlers below take the Namespace.

import json
import sys
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import yaml

from copalpm.config import DATA_DIR, SESSIONS_LOG, REGISTRY
from copalpm.project_record import save_yaml


class ServiceDownError(RuntimeError):
    """Raised by _api() when the task-tracker HTTP service is unreachable."""


class ApiError(RuntimeError):
    """Raised by _api() when the service returns a non-2xx response."""
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── Service client ─────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    cfg_path = DATA_DIR / "config.json"
    if not cfg_path.exists():
        print("error: service not configured. Run `copalpm service install` first.", file=sys.stderr)
        sys.exit(1)
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _api(method: str, endpoint: str, body: dict | None = None) -> dict:
    """Make an authenticated request to the task-tracker service.

    Raises ServiceDownError if the daemon is unreachable, ApiError on non-2xx.
    Top-level CLI handlers translate these into user-facing exits.
    """
    cfg  = _load_cfg()
    port = cfg.get("port", 5123)
    url  = f"http://127.0.0.1:{port}{endpoint}"
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-API-Key":     cfg["api_key"],
            "Content-Type":  "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        try:
            err = json.loads(body_text)
            msg = err.get("error") or err.get("hint") or body_text
        except Exception:
            msg = body_text
        raise ApiError(e.code, msg)
    except urllib.error.URLError as e:
        raise ServiceDownError(str(e))


def _exit_on_service_error(fn):
    """Decorator: translate ServiceDownError/ApiError to CLI-friendly exits."""
    def wrapper(args):
        try:
            return fn(args)
        except ServiceDownError:
            print("error: task-tracker service is not running.", file=sys.stderr)
            print("tip:   run `copalpm service install` to set it up, or check `copalpm service status`",
                  file=sys.stderr)
            sys.exit(1)
        except ApiError as e:
            print(f"error: {e.code} — {e.message}", file=sys.stderr)
            sys.exit(1)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ── Registry helpers ──────────────────────────────────────────────────────────

def _project_name(pid: str) -> str:
    """Return the human-readable project name for a given ID, or the ID itself."""
    try:
        items = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for item in items:
            if item.get("id") == pid:
                return item.get("name") or pid
    except Exception:
        pass
    return pid


# ── Project detection ──────────────────────────────────────────────────────────

def _find_project_id_from(start: Path) -> str | None:
    """Walk up from `start` looking for project.yaml. Return its id, or None."""
    start = start.resolve()
    for directory in [start, *start.parents]:
        candidate = directory / "project.yaml"
        if candidate.exists():
            with candidate.open(encoding="utf-8") as f:
                record = yaml.safe_load(f)
            return record.get("id")
    return None


def _find_phase_from(start: Path) -> str | None:
    """Walk up from `start` looking for project.yaml; return the latest phase."""
    start = start.resolve()
    for directory in [start, *start.parents]:
        candidate = directory / "project.yaml"
        if candidate.exists():
            with candidate.open(encoding="utf-8") as f:
                record = yaml.safe_load(f)
            log = record.get("phase_log") or []
            return log[-1]["phase"] if log else None
    return None


def find_project_id_from_cwd() -> str | None:
    """Walk up from CWD looking for project.yaml. Return its id, or None."""
    return _find_project_id_from(Path.cwd())


def resolve_project_id(args) -> str:
    """Return project ID from --project flag or CWD walk. Exits if not found."""
    if hasattr(args, "project") and args.project:
        return args.project
    pid = find_project_id_from_cwd()
    if not pid:
        print("error: no project.yaml found in this directory or any parent.", file=sys.stderr)
        print("tip:   run from inside a project folder, or use --project <id>", file=sys.stderr)
        sys.exit(1)
    return pid


def current_phase_from_cwd() -> str | None:
    """Read current phase from project.yaml in CWD walk."""
    return _find_phase_from(Path.cwd())


# ── Helpers ────────────────────────────────────────────────────────────────────

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fmt_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0m"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def elapsed_seconds(start_iso: str) -> int:
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    return int((datetime.now(timezone.utc) - start).total_seconds())


# ── Commands ───────────────────────────────────────────────────────────────────

@_exit_on_service_error
def cmd_start(args):
    pid   = resolve_project_id(args)
    phase = args.phase or current_phase_from_cwd()

    payload = {
        "projectId":   pid,
        "description": args.description,
        "tool":        args.tool,
        "phase":       phase,
    }
    resp = _api("POST", "/start", payload)

    stopped_prev = resp.get("stopped_prev")
    if stopped_prev:
        prev_pid  = stopped_prev.get("project_id", "")
        prev_secs = int(stopped_prev.get("duration_sec", 0))
        print(
            f"■  Stopped {_project_name(prev_pid)} ({prev_pid}) — "
            f"{fmt_duration(prev_secs)} logged"
        )

    desc_str  = f"  {args.description}" if args.description else ""
    tool_str  = f"  [{args.tool}]" if args.tool else ""
    phase_str = f"  ({phase})" if phase else ""
    print(f"▶  {pid}{desc_str}{tool_str}{phase_str}")


@_exit_on_service_error
def cmd_stop(args):
    resp = _api("POST", "/stop", {"reason": "manual"})
    if resp.get("stopped"):
        dur     = resp.get("duration_sec")
        pid     = resp.get("project_id", "")
        dur_str = fmt_duration(dur) if dur is not None else "?"
        print(f"■  Stopped.  {dur_str} logged  ({_project_name(pid)})")
    else:
        print("No active session to stop.")


def cmd_status(args):
    try:
        state = _api("GET", "/state")
    except (ServiceDownError, ApiError):
        print("  Status unavailable (service not running).")
        return

    if not state:
        print("  No active session.")
        return

    elapsed  = elapsed_seconds(state["start"])
    desc     = state.get("description") or "—"
    tool     = state.get("tool")
    phase    = state.get("phase")
    tool_str  = f"  [{tool}]" if tool else ""
    phase_str = f"  ({phase})" if phase else ""

    pid = state["project_id"]
    print(f"  Project : {_project_name(pid)}  ({pid})")
    print(f"  Task    : {desc}{tool_str}{phase_str}")
    print(f"  Elapsed : {fmt_duration(elapsed)}")


def cmd_log(args):
    """Write a manual time entry directly to project.yaml (no service required)."""
    current = Path.cwd().resolve()
    yaml_path = None
    for directory in [current, *current.parents]:
        candidate = directory / "project.yaml"
        if candidate.exists():
            yaml_path = candidate
            break

    if not yaml_path:
        print("error: no project.yaml found. Run from inside a project folder.", file=sys.stderr)
        sys.exit(1)

    with yaml_path.open(encoding="utf-8") as f:
        record = yaml.safe_load(f)

    phase      = args.phase or (record.get("phase_log") or [{}])[-1].get("phase")
    now        = datetime.now(timezone.utc)
    started_at = now.isoformat().replace("+00:00", "Z")  # approximate — manual entry
    duration_s = int(args.duration_min) * 60

    entry = {
        "session_id":   f"M-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
        "started_at":   started_at,
        "ended_at":     started_at,   # same — manual, no exact window
        "duration_sec": duration_s,
        "phase":        phase,
        "description":  args.description,
        "tool":         args.tool,
        "stop_reason":  "manual_log",
    }

    record.setdefault("time_entries", []).append(entry)
    save_yaml(yaml_path, record)

    print(f"  Logged {args.duration_min} min — {args.description}")
    total_s = sum(int(e.get("duration_sec", 0)) for e in record["time_entries"])
    print(f"  Total  : {fmt_duration(total_s)}")


# Argparse setup and dispatch live in cli.py; this module only exports cmd_* handlers.
