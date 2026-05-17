# src/copalpm/deliver_cli.py
# `copalpm deliver` — log delivered assets into project.yaml's deliverables.
#
# Each deliverable entry is a delivery *package* — a list of file paths plus
# metadata (type, recipient, name, notes). Storing multiple files per entry
# matches how VFX/motion deliveries actually ship (hero render + proxy + spec
# sheet bundled together).
#
# Usage (from inside a project folder):
#   copalpm deliver "Final_v3.mp4"
#   copalpm deliver "Final_v3.mp4" "Final_v3_proxy.mp4" "spec.pdf"
#   copalpm deliver "Final_v3.mp4" --final --to broadcast
#   copalpm deliver "files...*" --name "Episode 7 final delivery"
#
# Legacy entries with `path: str` (pre-2026-05-17) are soft-migrated on read
# via normalize_deliverable. New writes always use `paths: list[str]`.

import sys
from datetime import datetime, timezone
from pathlib import Path

from .project_record import save_yaml, load_yaml

VALID_RECIPIENTS = {"internal", "client", "broadcast"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _find_project_yaml() -> Path:
    current = Path.cwd().resolve()
    for directory in [current, *current.parents]:
        candidate = directory / "project.yaml"
        if candidate.exists():
            return candidate
    print("error: no project.yaml found in this directory or any parent.", file=sys.stderr)
    print("tip:   run from inside a project folder, or use --file <path>", file=sys.stderr)
    sys.exit(1)


def _relativize(p: Path, root: Path) -> str:
    """Return p relative to root when under root, absolute string otherwise.

    Used by both the CLI and the shell-trigger verb so deliverables survive
    `project move` and stay portable across machines.
    """
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return str(p)


# ── Normalization (legacy → new schema) ──────────────────────────────────────

def normalize_deliverable(entry: dict) -> dict:
    """Coerce a deliverable entry into the v2 schema (`paths: list[str]`).

    Rules:
      - `paths` present → wins; `path` is dropped on collision.
      - `paths` absent, `path` present → `paths = [path]`.
      - Empty / whitespace path strings are filtered out.
      - Malformed entries (no `paths` and no `path`, or `paths == []` after
        filtering) are kept with `paths: []` so the TUI can surface them
        rather than silently dropping user history.
    """
    e = dict(entry)
    if "paths" in e:
        e.pop("path", None)
    elif "path" in e:
        e["paths"] = [e.pop("path")]
    raw = e.get("paths") or []
    if isinstance(raw, str):
        raw = [raw]
    e["paths"] = [str(p) for p in raw if p and str(p).strip()]
    return e


def normalize_deliverables(record: dict) -> dict:
    """Normalize every entry in `record['deliverables']` in place."""
    record["deliverables"] = [
        normalize_deliverable(d) for d in (record.get("deliverables") or [])
    ]
    return record


# ── Command handler ──────────────────────────────────────────────────────────

def cmd_deliver(args):
    """Log a delivered asset (or bundle of assets) into the project record.

    `args.path` is a list of one or more file paths (argparse nargs="+").
    All paths land in a single `deliverables` entry's `paths` list.
    """
    if args.to not in VALID_RECIPIENTS:
        print(f"error: invalid recipient '{args.to}'. Choose: internal, client, broadcast",
              file=sys.stderr)
        sys.exit(1)

    paths_arg = args.path if isinstance(args.path, list) else [args.path]
    if not paths_arg:
        print("error: at least one path is required.", file=sys.stderr)
        sys.exit(1)

    yaml_path = Path(args.file) if args.file else _find_project_yaml()
    if not yaml_path.exists():
        print(f"error: file not found: {yaml_path}", file=sys.stderr)
        sys.exit(1)

    record = load_yaml(yaml_path)
    normalize_deliverables(record)

    project_root = yaml_path.parent
    stored_paths = [_relativize(Path(p), project_root) for p in paths_arg]
    name = args.name or Path(paths_arg[0]).stem

    entry = {
        "name":         name,
        "paths":        stored_paths,
        "type":         "final" if args.final else "draft",
        "recipient":    args.to,
        "delivered_at": _iso_now(),
        "notes":        args.note or "",
    }

    record.setdefault("deliverables", []).append(entry)
    save_yaml(yaml_path, record)

    kind  = entry["type"]
    n     = len(stored_paths)
    files_str = "1 file" if n == 1 else f"{n} files"
    total = len(record["deliverables"])
    print(f"logged: {name}  [{kind} -> {args.to}]  ({files_str}, {_iso_now()[:10]})")
    print(f"Total deliverables: {total}")
