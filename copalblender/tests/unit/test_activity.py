"""Unit tests for the activity probes (cursor + foreground window).

The Windows/Quartz/xdotool implementations are exercised via monkeypatch —
we don't actually call ctypes or spawn subprocesses in unit tests. The
goal is to verify each platform's branching + the ``None`` fallback contract.
"""

import subprocess

import pytest

import activity  # vendored module on sys.path via conftest.py


# ── get_cursor_pos dispatch ────────────────────────────────────────────────────

def test_get_cursor_pos_dispatches_windows(monkeypatch):
    monkeypatch.setattr(activity.platform, "system", lambda: "Windows")
    monkeypatch.setattr(activity, "_windows_cursor_pos", lambda: (100, 200))
    assert activity.get_cursor_pos() == (100, 200)


def test_get_cursor_pos_dispatches_macos(monkeypatch):
    monkeypatch.setattr(activity.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(activity, "_macos_cursor_pos", lambda: None)
    assert activity.get_cursor_pos() is None


def test_get_cursor_pos_dispatches_linux(monkeypatch):
    monkeypatch.setattr(activity.platform, "system", lambda: "Linux")
    monkeypatch.setattr(activity, "_linux_cursor_pos", lambda: (5, 10))
    assert activity.get_cursor_pos() == (5, 10)


# ── _linux_cursor_pos parsing ──────────────────────────────────────────────────

def _fake_run(stdout="", returncode=0, raises=None):
    def run(*args, **kwargs):
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(args[0], returncode, stdout, "")
    return run


def test_linux_cursor_parses_xdotool_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="X=123\nY=456\nSCREEN=0\nWINDOW=42\n"))
    assert activity._linux_cursor_pos() == (123, 456)


def test_linux_cursor_returns_none_when_xdotool_missing(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(raises=FileNotFoundError()))
    assert activity._linux_cursor_pos() is None


def test_linux_cursor_returns_none_on_timeout(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(raises=subprocess.TimeoutExpired(cmd=["xdotool"], timeout=2.0)))
    assert activity._linux_cursor_pos() is None


def test_linux_cursor_returns_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="", returncode=1))
    assert activity._linux_cursor_pos() is None


def test_linux_cursor_returns_none_when_no_xy_in_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="SCREEN=0\nWINDOW=42\n"))
    assert activity._linux_cursor_pos() is None


def test_linux_cursor_returns_none_on_unparseable_value(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="X=abc\nY=456\n"))
    assert activity._linux_cursor_pos() is None


# ── is_blender_focused dispatch ────────────────────────────────────────────────

def test_focus_dispatches_windows(monkeypatch):
    monkeypatch.setattr(activity.platform, "system", lambda: "Windows")
    monkeypatch.setattr(activity, "_windows_is_focused", lambda: True)
    assert activity.is_blender_focused() is True


def test_focus_dispatches_macos(monkeypatch):
    monkeypatch.setattr(activity.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(activity, "_macos_is_focused", lambda: False)
    assert activity.is_blender_focused() is False


def test_focus_dispatches_linux(monkeypatch):
    monkeypatch.setattr(activity.platform, "system", lambda: "Linux")
    monkeypatch.setattr(activity, "_linux_is_focused", lambda: None)
    assert activity.is_blender_focused() is None


# ── _macos_is_focused parsing ──────────────────────────────────────────────────

def test_macos_focus_blender_frontmost(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="Blender\n"))
    assert activity._macos_is_focused() is True


def test_macos_focus_other_app(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="Safari\n"))
    assert activity._macos_is_focused() is False


def test_macos_focus_no_osascript(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(raises=FileNotFoundError()))
    assert activity._macos_is_focused() is None


# ── _linux_is_focused parsing ──────────────────────────────────────────────────

def test_linux_focus_blender_window(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="Blender 4.5 - scene.blend\n"))
    assert activity._linux_is_focused() is True


def test_linux_focus_other_window(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="Firefox\n"))
    assert activity._linux_is_focused() is False


def test_linux_focus_no_xdotool(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(raises=FileNotFoundError()))
    assert activity._linux_is_focused() is None


def test_linux_focus_wayland_returns_none(monkeypatch):
    """xdotool exits non-zero on Wayland sessions where it can't query the WM."""
    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1))
    assert activity._linux_is_focused() is None
