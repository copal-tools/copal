---
description: One-shot situational awareness — reads project status memory, git state, and any pending handoff note.
---

# /copal-status

**When to use:** at the start of a session, or any time you've lost track of where the project is.

## Procedure

Run these in parallel and report a concise summary:

1. **Read** `C:\Users\Sifdone\.claude\projects\E--Development-copal\memory\project_status.md` — current phase + active work
2. **Read** `.claude/handoff.md` (if it exists) — the last session's handoff note
3. **Bash** `git status` — current working-tree state
4. **Bash** `git branch --show-current` — current branch
5. **Bash** `git log --oneline -5` — last five commits
6. **Read** the umbrella `CLAUDE.md` Status table — phase progress at a glance

## Output format

```
📍 Branch: <branch>           Phase: <N> (<status>)
📝 Working tree: <clean | N changed files>
🕒 Recent commits:
  <hash> <subject>
  ...

🚧 Last handoff (if any): <one-line summary from handoff.md>

➡️  Suggested next action: <inferred from handoff.md or current branch state>
```

Keep it under 15 lines. The user is using this to get oriented, not to read a report.
