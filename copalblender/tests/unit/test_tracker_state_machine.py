"""Unit tests for the tracker state machine.

Each test resets state before driving the machine with a sequence of events.
The "client" is a mock that returns canned whose() responses; we never spawn
a real subprocess.
"""

from types import SimpleNamespace

import pytest

import tracker  # vendored module on sys.path via conftest.py


# ── Helpers ────────────────────────────────────────────────────────────────────

class FakeClient:
    """Test stand-in for copalpm_client. ``whose_map`` maps path → match dict."""

    def __init__(self, whose_map: dict[str, dict | None] | None = None):
        self.whose_map = whose_map or {}
        self.calls: list[str] = []

    def whose(self, path):
        self.calls.append(path)
        return self.whose_map.get(path, None)


def _ctx(
    *,
    client: FakeClient,
    cursor_pos=None,
    is_focused=True,
    now: float = 1000.0,
    current_filepath: str | None = None,
    prefs_overrides=None,
):
    prefs = SimpleNamespace(
        ping_interval_sec=60,
        unfocus_stop_sec=300,
        cursor_static_pings=2,
    )
    if prefs_overrides:
        for k, v in prefs_overrides.items():
            setattr(prefs, k, v)
    return SimpleNamespace(
        client=client,
        prefs=prefs,
        cursor_pos=cursor_pos,
        is_focused=is_focused,
        now=now,
        current_filepath=current_filepath,
    )


@pytest.fixture(autouse=True)
def _reset_state():
    tracker.reset_state()
    yield
    tracker.reset_state()


# ── file_loaded ────────────────────────────────────────────────────────────────

def test_file_loaded_starts_session_for_project_file():
    client = FakeClient({"/projA/scene.blend": {"project_id": "PROJ-A"}})
    acts = tracker.handle_event(("file_loaded", "/projA/scene.blend"), _ctx(client=client))
    assert acts == [("start", "PROJ-A")]
    assert tracker.state["tracking_project_id"] == "PROJ-A"


def test_file_loaded_same_project_is_no_op():
    """Opening another file in the same project shouldn't issue start/stop."""
    client = FakeClient({"/projA/scene.blend": {"project_id": "PROJ-A"},
                         "/projA/render.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/projA/scene.blend"), _ctx(client=client))
    acts = tracker.handle_event(("file_loaded", "/projA/render.blend"), _ctx(client=client))
    assert acts == []
    assert tracker.state["tracking_project_id"] == "PROJ-A"


def test_file_loaded_different_project_switches():
    """Daemon auto-switches; we just emit a fresh ("start", new_pid)."""
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"},
                         "/B/y.blend": {"project_id": "PROJ-B"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    acts = tracker.handle_event(("file_loaded", "/B/y.blend"), _ctx(client=client))
    assert acts == [("start", "PROJ-B")]
    assert tracker.state["tracking_project_id"] == "PROJ-B"


def test_file_loaded_unregistered_stops_active_session():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}, "/random.blend": None})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    acts = tracker.handle_event(("file_loaded", "/random.blend"), _ctx(client=client))
    assert acts == [("stop", "manual")]
    assert tracker.state["tracking_project_id"] is None


def test_file_loaded_unregistered_with_no_session_is_no_op():
    client = FakeClient({})
    acts = tracker.handle_event(("file_loaded", "/random.blend"), _ctx(client=client))
    assert acts == []
    assert tracker.state["tracking_project_id"] is None


def test_file_loaded_untitled_stops_session():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    acts = tracker.handle_event(("file_loaded", None), _ctx(client=client))
    assert acts == [("stop", "manual")]


def test_file_loaded_empty_string_treated_as_untitled():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    acts = tracker.handle_event(("file_loaded", ""), _ctx(client=client))
    assert acts == [("stop", "manual")]


# ── file_saved ─────────────────────────────────────────────────────────────────

def test_file_saved_into_new_project_switches():
    """Save As from /A/x.blend (PROJ-A) into /B/y.blend (PROJ-B) → start B."""
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"},
                         "/B/y.blend": {"project_id": "PROJ-B"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    acts = tracker.handle_event(("file_saved", "/B/y.blend"), _ctx(client=client))
    assert acts == [("start", "PROJ-B")]


def test_file_saved_same_project_is_no_op():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    acts = tracker.handle_event(("file_saved", "/A/x.blend"), _ctx(client=client))
    assert acts == []


# ── file_closing ───────────────────────────────────────────────────────────────

def test_file_closing_is_no_op():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    acts = tracker.handle_event(("file_closing", "/B/y.blend"), _ctx(client=client))
    assert acts == []
    assert tracker.state["tracking_project_id"] == "PROJ-A"


# ── tick — active session: ping + checks ──────────────────────────────────────

def test_tick_active_emits_ping():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    acts = tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(50, 50)))
    assert ("ping",) in acts


def test_tick_active_cursor_moves_no_stop():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(50, 50)))
    acts = tracker.handle_event(("tick", 1060), _ctx(client=client, cursor_pos=(80, 80)))
    assert ("stop", "inactivity") not in acts
    assert tracker.state["tracking_project_id"] == "PROJ-A"


def test_tick_active_cursor_static_two_ticks_stops():
    """Default cursor_static_pings=2 → two ticks with same cursor → stop."""
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    # First tick: cursor at (50,50), no last_cursor yet → run=1
    tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(50, 50)))
    # Second tick: cursor at (50,50), same as last → run=2 → STOP
    acts = tracker.handle_event(("tick", 1060), _ctx(client=client, cursor_pos=(50, 50)))
    assert ("stop", "inactivity") in acts
    assert tracker.state["tracking_project_id"] is None


def test_tick_active_cursor_static_threshold_three(monkeypatch):
    """With cursor_static_pings=3, three identical samples needed."""
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    ctx = _ctx(client=client, cursor_pos=(50, 50), prefs_overrides={"cursor_static_pings": 3})
    tracker.handle_event(("tick", 1000), ctx)
    acts2 = tracker.handle_event(("tick", 1060), ctx)
    assert ("stop", "inactivity") not in acts2
    acts3 = tracker.handle_event(("tick", 1120), ctx)
    assert ("stop", "inactivity") in acts3


def test_tick_active_cursor_resets_run_on_movement():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(50, 50)))
    # User moves cursor — run resets to 1.
    tracker.handle_event(("tick", 1060), _ctx(client=client, cursor_pos=(80, 80)))
    # User holds still — run becomes 2 → stop only on the SECOND match.
    acts = tracker.handle_event(("tick", 1120), _ctx(client=client, cursor_pos=(80, 80)))
    assert ("stop", "inactivity") in acts


def test_tick_active_none_cursor_skips_static_check():
    """Wayland / unsupported platform: cursor_pos=None → no cursor-static stop."""
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    for t in (1000, 1060, 1120, 1180):
        acts = tracker.handle_event(("tick", t), _ctx(client=client, cursor_pos=None))
        assert ("stop", "inactivity") not in acts
    assert tracker.state["tracking_project_id"] == "PROJ-A"


# ── tick — active session: unfocus check ──────────────────────────────────────

def test_tick_active_unfocus_below_threshold_no_stop():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    # First unfocus tick — records unfocus_since.
    tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(10, 10), is_focused=False))
    # 100 sec later, still under default 300 sec threshold — no stop.
    acts = tracker.handle_event(("tick", 1100), _ctx(client=client, cursor_pos=(20, 20), is_focused=False))
    assert ("stop", "inactivity") not in acts


def test_tick_active_unfocus_above_threshold_stops():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(10, 10), is_focused=False))
    # 301 sec later, above default 300 — STOP.
    acts = tracker.handle_event(("tick", 1301), _ctx(client=client, cursor_pos=(20, 20), is_focused=False))
    assert ("stop", "inactivity") in acts
    assert tracker.state["tracking_project_id"] is None


def test_tick_active_refocus_resets_unfocus_timer():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(10, 10), is_focused=False))
    # Refocused at 1100 — unfocus_since clears.
    tracker.handle_event(("tick", 1100), _ctx(client=client, cursor_pos=(20, 20), is_focused=True))
    assert tracker.state["unfocus_since"] is None
    # Re-unfocus at 1200, then 1450 (=250s after 1200) — still under 300.
    tracker.handle_event(("tick", 1200), _ctx(client=client, cursor_pos=(30, 30), is_focused=False))
    acts = tracker.handle_event(("tick", 1450), _ctx(client=client, cursor_pos=(40, 40), is_focused=False))
    assert ("stop", "inactivity") not in acts


def test_tick_active_focus_unknown_skips_check():
    """is_focused=None (platform can't tell) shouldn't accumulate unfocus time."""
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    # Pass distinct cursor positions each tick so the cursor-static check
    # doesn't fire — we're isolating the unfocus check here.
    for t, pos in [(1000, (10, 10)), (2000, (20, 20)), (5000, (30, 30))]:
        acts = tracker.handle_event(("tick", t), _ctx(client=client, cursor_pos=pos, is_focused=None))
        assert ("stop", "inactivity") not in acts


# ── tick — idle: cursor movement restarts ─────────────────────────────────────

def test_tick_idle_cursor_movement_restarts_session():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    # File loaded, tick twice with same cursor → stop fires.
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(50, 50)))
    tracker.handle_event(("tick", 1060), _ctx(client=client, cursor_pos=(50, 50)))
    assert tracker.state["tracking_project_id"] is None
    # Now idle. Cursor moves → restart.
    acts = tracker.handle_event(
        ("tick", 1120),
        _ctx(client=client, cursor_pos=(80, 80), current_filepath="/A/x.blend"),
    )
    assert ("start", "PROJ-A") in acts
    assert tracker.state["tracking_project_id"] == "PROJ-A"


def test_tick_idle_cursor_still_no_restart():
    """If the cursor doesn't move while idle, no restart."""
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(50, 50)))
    tracker.handle_event(("tick", 1060), _ctx(client=client, cursor_pos=(50, 50)))
    acts = tracker.handle_event(
        ("tick", 1120),
        _ctx(client=client, cursor_pos=(50, 50), current_filepath="/A/x.blend"),
    )
    assert ("start", "PROJ-A") not in acts


def test_tick_idle_no_filepath_no_restart():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(50, 50)))
    tracker.handle_event(("tick", 1060), _ctx(client=client, cursor_pos=(50, 50)))
    # Current filepath is None (e.g. user opened an untitled scene since the stop).
    acts = tracker.handle_event(
        ("tick", 1120),
        _ctx(client=client, cursor_pos=(80, 80), current_filepath=None),
    )
    assert all(a[0] != "start" for a in acts)


def test_tick_idle_unregistered_file_no_restart():
    """Cursor movement on an untracked file doesn't restart."""
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}, "/random.blend": None})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(50, 50)))
    tracker.handle_event(("tick", 1060), _ctx(client=client, cursor_pos=(50, 50)))
    acts = tracker.handle_event(
        ("tick", 1120),
        _ctx(client=client, cursor_pos=(80, 80), current_filepath="/random.blend"),
    )
    assert all(a[0] != "start" for a in acts)


# ── quit ───────────────────────────────────────────────────────────────────────

def test_quit_with_active_session_emits_stop():
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    acts = tracker.handle_event(("quit",), _ctx(client=client))
    assert acts == [("stop", "manual")]
    assert tracker.state["tracking_project_id"] is None


def test_quit_without_active_session_is_no_op():
    client = FakeClient({})
    acts = tracker.handle_event(("quit",), _ctx(client=client))
    assert acts == []


# ── End-to-end scenario ────────────────────────────────────────────────────────

def test_full_lifecycle_scenario():
    """Open project file → tick → minimize → wait → resume → close."""
    client = FakeClient({"/A/x.blend": {"project_id": "PROJ-A"}})
    # 1. File loaded
    a = tracker.handle_event(("file_loaded", "/A/x.blend"), _ctx(client=client))
    assert a == [("start", "PROJ-A")]
    # 2. Active tick, cursor moving — just a ping
    a = tracker.handle_event(("tick", 1000), _ctx(client=client, cursor_pos=(10, 10)))
    assert ("ping",) in a and not any(x[0] in ("start", "stop") for x in a)
    # 3. User minimizes — unfocused tick records unfocus_since
    a = tracker.handle_event(("tick", 1060), _ctx(client=client, cursor_pos=(20, 20), is_focused=False))
    assert ("ping",) in a
    # 4. 5 min later, unfocus stop fires
    a = tracker.handle_event(("tick", 1360), _ctx(client=client, cursor_pos=(20, 20), is_focused=False))
    assert ("stop", "inactivity") in a
    # 5. User comes back, moves cursor — idle restarts
    a = tracker.handle_event(
        ("tick", 1500),
        _ctx(client=client, cursor_pos=(99, 99), is_focused=True, current_filepath="/A/x.blend"),
    )
    assert ("start", "PROJ-A") in a
    # 6. Blender quits
    a = tracker.handle_event(("quit",), _ctx(client=client))
    assert a == [("stop", "manual")]
