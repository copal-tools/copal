#!/usr/bin/env python3
"""PreToolUse hook for Bash: block git commits that contain Co-Authored-By or
Generated-with-Claude trailers. See feedback_commits.md memory entry."""
import json
import re
import sys

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

if payload.get("tool_name") != "Bash":
    sys.exit(0)

command = payload.get("tool_input", {}).get("command", "")
if not re.match(r"^\s*git\s+commit\b", command):
    sys.exit(0)

forbidden = (
    re.compile(r"Co-Authored-By", re.IGNORECASE),
    re.compile(r"Generated with .*Claude", re.IGNORECASE),
    re.compile(r"\xf0\x9f\xa4\x96 Generated"),
)
for pattern in forbidden:
    if pattern.search(command):
        print(
            "Commit message contains a forbidden trailer (Co-Authored-By / "
            "'Generated with Claude'). The user prefers commits without these "
            "trailers — see feedback_commits.md memory. Re-run the commit "
            "with the trailer removed.",
            file=sys.stderr,
        )
        sys.exit(2)

sys.exit(0)
