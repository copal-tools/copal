#!/usr/bin/env python3
"""Stop hook: when the session's uncommitted changes touch product code in
copalvx or copalpm but no CLAUDE.md was modified, surface a one-line
reminder pointing at /copal-doc-check. Non-blocking."""
import json
import os
import subprocess
import sys

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

cwd = payload.get("cwd") or os.getcwd()


def changed_files():
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []
    files = []
    for line in result.stdout.splitlines():
        # porcelain format: "XY path" with optional rename "XY old -> new"
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path.replace("\\", "/"))
    return files


files = changed_files()
if not files:
    sys.exit(0)

CODE_PATTERNS = (
    "copalvx/client/copal_core/",
    "copalvx/server/app/",
    "copalpm/src/copalpm/",
)

code_changed = [f for f in files if any(p in f for p in CODE_PATTERNS)]
docs_changed = [
    f
    for f in files
    if f.endswith("CLAUDE.md") or f.endswith("WORKFLOW.md") or "/CLAUDE.md" in f
]

if code_changed and not docs_changed:
    areas = sorted(
        {
            "copalvx-client" if "copalvx/client/copal_core/" in f else
            "copalvx-server" if "copalvx/server/app/" in f else
            "copalpm" if "copalpm/src/copalpm/" in f else "other"
            for f in code_changed
        }
    )
    msg = (
        f"[copal hint] Uncommitted changes in {', '.join(areas)} but no "
        "CLAUDE.md updated. Consider /copal-doc-check before committing."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "additionalContext": msg,
                }
            }
        )
    )

sys.exit(0)
