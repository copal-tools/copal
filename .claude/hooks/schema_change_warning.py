#!/usr/bin/env python3
"""PostToolUse hook for Edit/Write: surface non-blocking warnings when
sensitive files are modified.

- docker-compose.yml -> remind about storage contract (mounted volumes)
- project.yaml       -> remind about validator + CLAUDE.md schema reference
- pm_hooks.py / copalvx_api.py -> remind to run /copal-cross-package
"""
import json
import sys

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

tool_name = payload.get("tool_name", "")
if tool_name not in ("Edit", "Write", "MultiEdit"):
    sys.exit(0)

file_path = payload.get("tool_input", {}).get("file_path", "")
file_path_norm = file_path.replace("\\", "/").lower()

messages = []

if file_path_norm.endswith("docker-compose.yml") or file_path_norm.endswith(
    "docker-compose.yaml"
):
    messages.append(
        "docker-compose.yml changed — verify the storage contract: every "
        "stateful container path on a named/mounted volume. The SeaweedFS "
        "filer leveldb specifically must be on the persistent host mount. "
        "See copalvx/server/DEPLOY.md."
    )

if file_path_norm.endswith("/project.yaml"):
    messages.append(
        "project.yaml changed — if this introduces a new top-level key, "
        "verify project_record.py validator accepts it and update "
        "copalpm/CLAUDE.md schema reference."
    )

if (
    file_path_norm.endswith("/pm_hooks.py")
    or file_path_norm.endswith("/copalvx_api.py")
):
    messages.append(
        "Cross-package contract file changed — run /copal-cross-package to "
        "verify both sides stay consistent. Both packages' CLAUDE.md must "
        "be updated in the same commit."
    )

if file_path_norm.endswith("/init_db.py") or (
    file_path_norm.endswith("/main.py") and "/server/app/" in file_path_norm
):
    messages.append(
        "CopalVX server schema/handler file changed — if DB schema changed "
        "this is a Class B deploy (see /copal-deploy). If a DELETE handler "
        "changed, verify FK delete order: projects -> commits -> "
        "project_files -> assets."
    )

if file_path_norm.endswith("/shell_integration.py"):
    messages.append(
        "shell_integration.py changed — Win11 24H2/25H2 silently filters "
        "new HKCU verbs. Any new shell verb must be written to HKLM."
    )

if not messages:
    sys.exit(0)

print(
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(f"[copal hint] {m}" for m in messages),
            }
        }
    )
)
sys.exit(0)
