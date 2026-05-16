"""Copal: time tracking — Blender addon.

Auto-starts a CopalPM time-tracking session for the project a .blend file
belongs to. Stops on Blender quit, file close (incl. switching to a file
in a different project), prolonged unfocus, or cursor inactivity.

This module is the entry point Blender loads. Pure-Python siblings
(tracker.py, copalpm_client.py, activity.py) are import-safe outside
Blender so they can be unit-tested directly.

This file ONLY runs inside Blender — it imports ``bpy`` at module scope.
The pytest suite never touches it.
"""

from __future__ import annotations

import atexit
import time
from types import SimpleNamespace

import bpy

from . import activity
from . import copalpm_client
from . import preferences
from . import status_panel
from . import tracker


bl_info = {
    "name": "Copal: time tracking",
    "author": "The Copal Tools Authors",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "Edit > Preferences > Add-ons",
    "category": "System",
    "description": "Auto-start CopalPM time tracking for the project this .blend belongs to.",
}


# ── Client wrapper ─────────────────────────────────────────────────────────────

class _ClientForTracker:
    """Adapter exposing only ``whose(path)`` to the tracker.

    The tracker stays agnostic about preferences; this wrapper threads the
    addon's ``copalpm_path_override`` preference into ``copalpm_client.whose``.
    """

    def __init__(self, override: str | None):
        self._override = override or None

    def whose(self, path: str) -> dict | None:
        return copalpm_client.whose(path, copalpm_path_override=self._override)


# ── Event dispatch ─────────────────────────────────────────────────────────────

def _run(event: tuple) -> None:
    """Build a tracker ctx and run one event through the state machine."""
    try:
        prefs = preferences.get(bpy.context)
    except Exception as e:
        print(f"[copal_blender] preferences unavailable: {e!r}")
        return

    if not getattr(prefs, "enabled", True):
        return

    try:
        ctx = SimpleNamespace(
            client=_ClientForTracker(prefs.copalpm_path_override),
            prefs=prefs,
            cursor_pos=activity.get_cursor_pos(),
            is_focused=activity.is_blender_focused(),
            current_filepath=bpy.data.filepath or None,
        )
        actions = tracker.handle_event(event, ctx)
    except copalpm_client.NotInstalledError:
        # whose() called from inside the tracker and copalpm isn't installed.
        # Silently no-op; the user can install copalpm whenever they're ready.
        return
    except Exception as e:
        print(f"[copal_blender] handle_event {event[0]} failed: {e!r}")
        return

    for action in actions:
        _dispatch_action(action)


def _dispatch_action(action: tuple) -> None:
    kind = action[0]
    try:
        if kind == "start":
            copalpm_client.start(action[1], tool="blender")
        elif kind == "stop":
            copalpm_client.stop(reason=action[1])
        elif kind == "ping":
            copalpm_client.ping()
    except copalpm_client.ServiceDownError:
        # Daemon not running — stay quiet; next tick may succeed.
        pass
    except copalpm_client.NotInstalledError:
        pass
    except copalpm_client.ApiError as e:
        print(f"[copal_blender] api {e.code}: {e.message}")
    except Exception as e:
        print(f"[copal_blender] dispatch {kind} failed: {e!r}")


# ── Blender handler thunks ─────────────────────────────────────────────────────

@bpy.app.handlers.persistent
def _on_load_post(_filepath):  # type: ignore[no-untyped-def]
    _run(("file_loaded", bpy.data.filepath or None))


@bpy.app.handlers.persistent
def _on_load_pre(filepath):  # type: ignore[no-untyped-def]
    _run(("file_closing", filepath or ""))


@bpy.app.handlers.persistent
def _on_save_post(_filepath):  # type: ignore[no-untyped-def]
    _run(("file_saved", bpy.data.filepath or ""))


def _on_quit():
    _run(("quit",))


# ── Timer ──────────────────────────────────────────────────────────────────────

def _tick() -> float:
    """Periodic timer body. Returns the interval until the next call."""
    interval = 60.0
    try:
        prefs = preferences.get(bpy.context)
        interval = float(getattr(prefs, "ping_interval_sec", 60))
        _run(("tick", time.monotonic()))
    except Exception as e:
        print(f"[copal_blender] tick error (continuing): {e!r}")
    return interval


# ── Registration ───────────────────────────────────────────────────────────────

_registered_handlers: list = []


def _safe_register_class(cls) -> None:
    """Register a Blender class, recovering from a stale "already registered" state.

    If a previous register() failed partway through (e.g. because bpy.data was
    restricted), the AddonPreferences class can stay registered behind the
    scenes. A naive register_class then raises ``ValueError`` on the next
    enable. Unregister first and retry to make this idempotent.
    """
    try:
        bpy.utils.register_class(cls)
    except ValueError:
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
        bpy.utils.register_class(cls)


def _fire_initial_event():
    """One-shot timer body: fires the synthetic file_loaded event after register() returns.

    During register() Blender wraps ``bpy.data`` in ``_RestrictData``, which
    blocks attribute access (``bpy.data.filepath`` raises AttributeError). The
    restriction lifts once register() completes, so we defer the initial
    event by 0.1s via the timer system. Returns ``None`` to stop the timer
    after one firing.
    """
    try:
        _run(("file_loaded", bpy.data.filepath or None))
    except Exception as e:
        print(f"[copal_blender] initial event failed: {e!r}")
    return None  # one-shot


def register() -> None:
    _safe_register_class(preferences.CopalAddonPreferences)
    status_panel.register_panel()

    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.handlers.load_pre.append(_on_load_pre)
    bpy.app.handlers.save_post.append(_on_save_post)
    _registered_handlers[:] = [
        (bpy.app.handlers.load_post, _on_load_post),
        (bpy.app.handlers.load_pre, _on_load_pre),
        (bpy.app.handlers.save_post, _on_save_post),
    ]

    atexit.register(_on_quit)

    bpy.app.timers.register(_tick, first_interval=2.0, persistent=True)

    # Pick up the file already open at enable time. Deferred via timer because
    # bpy.data is restricted during register() — see _fire_initial_event docstring.
    tracker.reset_state()
    bpy.app.timers.register(_fire_initial_event, first_interval=0.1)

    print("[copal_blender] registered")


def unregister() -> None:
    # Final stop on disable, before tearing handlers down.
    try:
        _run(("quit",))
    except Exception as e:
        print(f"[copal_blender] final stop failed: {e!r}")

    try:
        bpy.app.timers.unregister(_tick)
    except (ValueError, RuntimeError):
        pass

    for handler_list, fn in _registered_handlers:
        try:
            handler_list.remove(fn)
        except ValueError:
            pass
    _registered_handlers.clear()

    try:
        atexit.unregister(_on_quit)
    except Exception:
        pass

    status_panel.unregister_panel()
    try:
        bpy.utils.unregister_class(preferences.CopalAddonPreferences)
    except RuntimeError:
        pass

    print("[copal_blender] unregistered")
