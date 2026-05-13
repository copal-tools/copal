# src/copalpm/deliver_cli.py
# `copalpm deliver` — log a delivered asset into project.yaml's deliverables.
#
# Usage (from inside a project folder):
#   copalpm deliver "Final_v3.mp4"                          # draft -> client
#   copalpm deliver "Final_v3.mp4" --final                  # final -> client
#   copalpm deliver "Final_v3.mp4" --final --to broadcast   # final -> broadcast
#   copalpm deliver "Draft1.mp4" --to internal              # draft -> internal
#   copalpm deliver "file.mp4" --name "Custom Name" --note "colour-corrected"
#
# The argparse setup lives in cli.py; cmd_deliver() below is the handler.

import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


_YAML_HEADER = (
    "# project.yaml — Project Record v1\n"
    "# Reference: schema/project-record.yaml\n\n"
)

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


def cmd_deliver(args):
    """Log a delivered asset into the project record's deliverables array.

    Called by `copalpm deliver` via the unified cli.py dispatcher. Expects an
    argparse Namespace with: path, final, to, name, note, file.
    """
    if args.to not in VALID_RECIPIENTS:
        print(f"error: invalid recipient '{args.to}'. Choose: internal, client, broadcast",
              file=sys.stderr)
        sys.exit(1)

    # Resolve project.yaml
    yaml_path = Path(args.file) if args.file else _find_project_yaml()
    if not yaml_path.exists():
        print(f"error: file not found: {yaml_path}", file=sys.stderr)
        sys.exit(1)

    with yaml_path.open("r", encoding="utf-8") as f:
        record = yaml.safe_load(f) or {}

    # Derive metadata from the file path
    file_path = Path(args.path)
    name      = args.name or file_path.stem
    fmt       = file_path.suffix.lstrip(".").lower() or None

    entry = {
        "name":         name,
        "path":         str(file_path.resolve()) if file_path.exists() else str(file_path),
        "format":       fmt,
        "type":         "final" if args.final else "draft",
        "recipient":    args.to,
        "delivered_at": _iso_now(),
        "notes":        args.note or "",
    }

    record.setdefault("deliverables", []).append(entry)

    yaml_path.write_text(
        _YAML_HEADER + yaml.dump(
            record, default_flow_style=False, allow_unicode=True, sort_keys=False
        ),
        encoding="utf-8",
    )

    kind = "final" if args.final else "draft"
    total = len(record["deliverables"])
    print(f"logged: {name}  [{kind} -> {args.to}]  ({_iso_now()[:10]})")
    print(f"Total deliverables: {total}")
