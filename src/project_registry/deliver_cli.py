# src/project_registry/deliver_cli.py
# deliver — log a delivered asset into the project record's deliverables array.
#
# Usage (from inside a project folder):
#   deliver "Final_v3.mp4"                          # draft -> client
#   deliver "Final_v3.mp4" --final                  # final -> client
#   deliver "Final_v3.mp4" --final --to broadcast   # final -> broadcast
#   deliver "Draft1.mp4" --to internal              # draft -> internal
#   deliver "file.mp4" --name "Custom Name" --note "colour-corrected"

import argparse
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


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        prog="deliver",
        description="Log a delivered asset into the project record.",
    )
    ap.add_argument("path",
                    help="Path to the delivered file (used to derive name and format)")
    ap.add_argument("--final",  action="store_true",
                    help="Mark as final (default: draft)")
    ap.add_argument("--to",     metavar="RECIPIENT", default="client",
                    help="Recipient: internal | client | broadcast  (default: client)")
    ap.add_argument("--name",   metavar="NAME",
                    help="Display name (default: filename without extension)")
    ap.add_argument("--note",   metavar="TEXT",
                    help="Optional notes")
    ap.add_argument("--file",   metavar="PATH",
                    help="Explicit path to project.yaml (defaults to CWD walk)")

    args = ap.parse_args()

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


if __name__ == "__main__":
    main()
