---
description: Write a session handoff note to .claude/handoff.md so the next Claude session can pick up where this one left off.
---

# /copal-handoff

**When to use:** ending a session with work in progress, blocked, or context the next session shouldn't have to rediscover. The file is gitignored — kept on the branch, not committed.

## Procedure

1. **Gather state:**
   - `git status --porcelain` — uncommitted changes
   - `git diff --stat` — summary of unstaged work
   - `git log --oneline -10` — what shipped this session
   - `git branch --show-current` — current branch
   - Conversation context — what we were trying to do, what we learned, what's blocked

2. **Write `.claude/handoff.md`** in the worktree root:

   ```markdown
   # Handoff — <branch> — <YYYY-MM-DD HH:MM>

   ## Shipped this session
   - <commit subject> (<short hash>) — <1-line context>
   - ...

   ## In progress / blocked
   - **<file:area>** — <what's being done, what's blocking>
   - ...

   ## Next concrete action
   1. <specific first thing to do>
   2. ...

   ## Open questions for the user
   - <question, with enough context to answer cold>

   ## Useful context
   - <link to relevant CLAUDE.md section or external doc>
   - <gotchas hit this session worth flagging>
   ```

3. **Confirm** with the user that the handoff captures the right things before ending the session.

## Conventions

- One sentence per item — the next session is a colleague reading cold, not a stranger reading a novel.
- Reference files by path (`copal_core/sync.py:120`) so the next session can jump in instantly.
- "In progress" items belong here. "Done" items belong in commits, not handoff.
- If nothing is in flight, don't write the file. An empty handoff is worse than no handoff.
