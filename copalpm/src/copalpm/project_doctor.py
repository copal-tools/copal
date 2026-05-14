# src/copalpm/project_doctor.py
# `copalpm project doctor` — read-only health check that reports drift
# between the registry, on-disk project folders, and the sessions log.
#
# Helpers (`find_path_drift`, `find_orphan_sessions`) take their inputs
# explicitly so tests can hand them tmp_path fixtures without
# monkeypatching globals. `cmd_doctor` is the CLI glue.

import json
import sys
from pathlib import Path

from copalpm.config import SESSIONS_LOG
from copalpm.pm import load_registry


def find_path_drift(registry: list[dict]) -> list[dict]:
    """Return registry entries whose on-disk state is broken.

    Each result is {"id", "name", "path", "reason"} where reason is one of:
      "missing_path" — the registered folder does not exist
      "missing_yaml" — the folder exists but has no project.yaml
    Entries with no path field are also reported as "missing_path".
    """
    drift: list[dict] = []
    for entry in registry:
        pid  = entry.get("id")
        name = entry.get("name") or ""
        path = entry.get("path")
        if not path:
            drift.append({"id": pid, "name": name, "path": "", "reason": "missing_path"})
            continue
        p = Path(path)
        if not p.exists():
            drift.append({"id": pid, "name": name, "path": path, "reason": "missing_path"})
        elif not (p / "project.yaml").exists():
            drift.append({"id": pid, "name": name, "path": path, "reason": "missing_yaml"})
    return drift


def find_orphan_sessions(registry: list[dict], sessions_log: Path) -> dict[str, int]:
    """Return {project_id: session_count} for sessions whose project_id
    is not present in the registry.

    Returns an empty dict when the sessions log is missing or empty.
    Malformed JSON lines are skipped silently (mirrors `cmd_sync_time`).
    """
    if not sessions_log.exists():
        return {}

    known_ids = {e.get("id") for e in registry if e.get("id")}
    counts: dict[str, int] = {}
    with sessions_log.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                session = json.loads(line)
            except Exception:
                continue
            pid = session.get("project_id")
            if not pid or pid in known_ids:
                continue
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def cmd_doctor(args) -> None:
    """`copalpm project doctor` — print a drift report, exit 0 always."""
    registry = load_registry()
    drift    = find_path_drift(registry)
    orphans  = find_orphan_sessions(registry, SESSIONS_LOG)

    if not drift and not orphans:
        print("All checks passed.")
        return

    if drift:
        print(f"Path drift ({len(drift)} entries):")
        for d in drift:
            label = f'{d["id"]} "{d["name"]}"' if d["name"] else d["id"]
            if d["reason"] == "missing_path":
                print(f'  - {label} — missing folder: {d["path"] or "(no path)"}')
            elif d["reason"] == "missing_yaml":
                print(f'  - {label} — folder exists but project.yaml is missing: {d["path"]}')
        print("  tip: re-register the moved folder with `copalpm project register <path>`")
        print("       or drop the stale entry with `copalpm project remove <id>`.")

    if orphans:
        if drift:
            print()
        print(f"Orphan sessions ({len(orphans)} project_id group(s)):")
        for pid, count in sorted(orphans.items()):
            plural = "session" if count == 1 else "sessions"
            print(f"  - {pid} — {count} {plural} in sessions.jsonl")
        print("  tip: register the project (`copalpm project register <path>`)")
        print("       if it still lives on disk, otherwise the sessions remain in the log.")
