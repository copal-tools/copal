# src/copalpm/template_cli.py
"""CLI handlers for the `copalpm template` subcommand surface (list/export/import).

Editing happens in the TUI (see TemplateScreen / EditTemplateModal). This module
covers the sharing/transfer use case: list installed templates, export one to a
chosen path, import a YAML file authored elsewhere.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from copalpm import templates


def cmd_template_list(args) -> None:
    """List installed templates."""
    items = templates.load_all()
    if getattr(args, "json", False):
        rows = [
            {
                "id":          t["id"],
                "name":        t["name"],
                "fields":      len(t.get("fields", [])),
                "folders":     len(t.get("folders", [])),
                "file":        t.get("_filename"),
            }
            for t in items
        ]
        json.dump(rows, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return

    if not items:
        print("No templates installed.")
        return

    # Plain table
    rows = [
        ("ID", "NAME", "FIELDS", "FOLDERS", "FILE"),
    ]
    for t in items:
        rows.append((
            t["id"],
            t["name"],
            str(len(t.get("fields", []))),
            str(len(t.get("folders", []))),
            t.get("_filename", ""),
        ))
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    for i, row in enumerate(rows):
        print("  ".join(cell.ljust(widths[c]) for c, cell in enumerate(row)))
        if i == 0:
            print("  ".join("-" * widths[c] for c in range(len(row))))


def cmd_template_export(args) -> None:
    """Write a template YAML to a chosen path."""
    out_arg = getattr(args, "out", None)
    if out_arg is None:
        dst = Path.cwd() / f"{args.id}.yaml"
    else:
        dst = Path(out_arg).expanduser()
    try:
        written = templates.export_template(args.id, dst)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"wrote {written}")


def cmd_template_import(args) -> None:
    """Validate and copy a YAML file into the templates directory."""
    src = Path(args.path).expanduser()
    try:
        tmpl = templates.import_template(src, force=bool(getattr(args, "force", False)))
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"imported template '{tmpl['id']}' ({tmpl['name']}) "
          f"with {len(tmpl.get('fields', []))} field(s)")
