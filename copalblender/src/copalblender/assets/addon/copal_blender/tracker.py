"""Pure-function state machine driving the addon's tracking decisions.

The Blender side (in ``__init__.py``) feeds events into ``handle_event`` and
dispatches the returned actions to the HTTP client. Keeping this module
free of ``bpy`` makes it unit-testable from host Python.

Events
------
Each event is a tuple. The first element identifies the event:

* ``("file_loaded", path)`` — Blender finished loading ``path``. ``path``
  may be empty/None for the untitled startup scene.
* ``("file_closing", new_path)`` — ``load_pre`` fired. Always a no-op; the
  subsequent ``load_post`` event resolves the new file.
* ``("file_saved", path)`` — ``save_post`` fired. Re-resolves in case of a
  Save-As into a different project folder.
* ``("tick", now_seconds)`` — periodic timer fired. ``now_seconds`` is a
  monotonic timestamp (``time.monotonic()``).
* ``("quit",)`` — Blender is exiting (``atexit``).

Context object
--------------
``ctx`` must expose the following attributes (typically a ``SimpleNamespace``):

* ``client`` — an object with ``whose(path) -> dict | None`` (the HTTP
  client itself isn't used here; the dispatcher in ``__init__.py`` runs
  the returned actions against ``client.start/stop/ping``).
* ``prefs`` — the addon preferences object exposing
  ``ping_interval_sec``, ``unfocus_stop_sec``, ``cursor_static_pings``.
* ``cursor_pos`` — ``tuple[int, int] | None`` from ``activity.get_cursor_pos()``.
* ``is_focused`` — ``bool | None`` from ``activity.is_blender_focused()``.
* ``current_filepath`` — ``str | None``; the .blend file currently open.

For tick events, the monotonic timestamp comes from the event tuple's
second element (``event[1]``) — not from ``ctx``. Other event types
don't need a timestamp.

Actions
-------
``handle_event`` returns a list of action tuples:

* ``("start", project_id)`` — issue ``/start`` for this project.
* ``("stop", reason)`` — issue ``/stop`` with this reason.
* ``("ping",)`` — issue ``/ping`` to refresh the daemon's idle timer.

The dispatcher in ``__init__.py`` swallows ``ServiceDownError``,
``NotInstalledError`` and ``ApiError``, so the tracker doesn't need to
worry about those — but ``whose()`` exceptions DO propagate from here
(the dispatch handles them on its outer try/except).
"""

from __future__ import annotations

from typing import Any


# Module-level state. Mutated only by handle_event() / reset_state().
state: dict[str, Any] = {
    "tracking_project_id": None,  # str | None
    "last_cursor": None,           # tuple[int, int] | None
    "cursor_static_run": 0,        # length of current run of identical cursor samples
    "unfocus_since": None,         # monotonic seconds when unfocus started, or None
}


def reset_state() -> None:
    """Reset the module-level state. Tests should call this in setup."""
    state["tracking_project_id"] = None
    state["last_cursor"] = None
    state["cursor_static_run"] = 0
    state["unfocus_since"] = None


# ── Event dispatch ─────────────────────────────────────────────────────────────

def handle_event(event: tuple, ctx: Any) -> list[tuple]:
    kind = event[0]

    if kind == "file_loaded":
        return _resolve_path_change(event[1] if len(event) > 1 else None, ctx)

    if kind == "file_saved":
        return _resolve_path_change(event[1] if len(event) > 1 else None, ctx)

    if kind == "file_closing":
        # load_pre fires before the new file is necessarily on disk; load_post
        # in the next event resolves the actual project. Treat as no-op.
        return []

    if kind == "tick":
        now = float(event[1]) if len(event) > 1 else 0.0
        return _on_tick(ctx, now)

    if kind == "quit":
        return _on_quit()

    return []


# ── Path-change handler (file_loaded / file_saved) ─────────────────────────────

def _resolve_path_change(path: str | None, ctx: Any) -> list[tuple]:
    """Resolve a new .blend path → start/stop/no-op."""
    acts: list[tuple] = []

    if not path:
        # Untitled / empty filepath: stop any active session.
        if state["tracking_project_id"]:
            acts.append(("stop", "manual"))
            state["tracking_project_id"] = None
            _reset_idle_signals()
        return acts

    match = ctx.client.whose(path)
    if match is None:
        # File is not in any registered project: stop if anything's tracking.
        if state["tracking_project_id"]:
            acts.append(("stop", "manual"))
            state["tracking_project_id"] = None
            _reset_idle_signals()
        return acts

    pid = match["project_id"]
    if pid != state["tracking_project_id"]:
        # /start auto-switches on the daemon side — no explicit stop needed.
        acts.append(("start", pid))
        state["tracking_project_id"] = pid
        _reset_idle_signals()
    return acts


# ── Tick handler ───────────────────────────────────────────────────────────────

def _on_tick(ctx: Any, now: float) -> list[tuple]:
    if state["tracking_project_id"]:
        return _on_tick_active(ctx, now)
    return _on_tick_idle(ctx)


def _on_tick_active(ctx: Any, now: float) -> list[tuple]:
    """Tick handler when a session is currently being tracked."""
    acts: list[tuple] = [("ping",)]

    # ── Cursor-static check (Check 2) ──────────────────────────────────────
    if ctx.cursor_pos is not None:
        if state["last_cursor"] is not None and ctx.cursor_pos == state["last_cursor"]:
            state["cursor_static_run"] += 1
        else:
            state["cursor_static_run"] = 1
        state["last_cursor"] = ctx.cursor_pos

        threshold = max(2, int(getattr(ctx.prefs, "cursor_static_pings", 2)))
        if state["cursor_static_run"] >= threshold:
            acts.append(("stop", "inactivity"))
            state["tracking_project_id"] = None
            state["cursor_static_run"] = 0
            state["unfocus_since"] = None
            return acts  # session ended; skip unfocus check this tick

    # ── Unfocus check (Check 1) ────────────────────────────────────────────
    if ctx.is_focused is False:
        if state["unfocus_since"] is None:
            state["unfocus_since"] = now
        elif now - state["unfocus_since"] >= int(getattr(ctx.prefs, "unfocus_stop_sec", 300)):
            acts.append(("stop", "inactivity"))
            state["tracking_project_id"] = None
            state["unfocus_since"] = None
            state["cursor_static_run"] = 0
    elif ctx.is_focused is True:
        state["unfocus_since"] = None
    # is_focused is None → leave unfocus_since untouched (skip the check)

    return acts


def _on_tick_idle(ctx: Any) -> list[tuple]:
    """Tick handler while no session is tracking. Watch for cursor resume."""
    acts: list[tuple] = []

    if (
        ctx.cursor_pos is not None
        and state["last_cursor"] is not None
        and ctx.cursor_pos != state["last_cursor"]
        and ctx.current_filepath
    ):
        match = ctx.client.whose(ctx.current_filepath)
        if match:
            acts.append(("start", match["project_id"]))
            state["tracking_project_id"] = match["project_id"]
            state["cursor_static_run"] = 1
            state["unfocus_since"] = None

    if ctx.cursor_pos is not None:
        state["last_cursor"] = ctx.cursor_pos
    return acts


# ── Quit handler ───────────────────────────────────────────────────────────────

def _on_quit() -> list[tuple]:
    if state["tracking_project_id"]:
        acts: list[tuple] = [("stop", "manual")]
        state["tracking_project_id"] = None
        _reset_idle_signals()
        return acts
    return []


# ── Helpers ────────────────────────────────────────────────────────────────────

def _reset_idle_signals() -> None:
    """Reset the cursor-static + unfocus counters on state transitions."""
    state["cursor_static_run"] = 0
    state["unfocus_since"] = None
