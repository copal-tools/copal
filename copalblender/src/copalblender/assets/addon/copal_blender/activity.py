"""OS-level cursor + foreground-window probes used by the activity heuristics.

Each public function returns either a concrete value or ``None``. A ``None``
return means "this platform can't answer right now" — the tracker treats
that as a signal to skip that particular check, so partial functionality
never blocks the rest.

Coverage matrix:

  Windows      cursor: ctypes GetCursorPos          focus: ctypes GetForegroundWindow + PID match
  macOS        cursor: Quartz if PyObjC available   focus: osascript System Events
                       else None
  Linux X11    cursor: xdotool                      focus: xdotool getactivewindow
  Linux Wayl.  cursor: None                         focus: None

The PyObjC-on-Blender's-Python case is rare in practice — Blender ships
its own Python without PyObjC. Returning ``None`` when Quartz isn't
importable keeps the addon functional with focus + file-event triggers
only; the cursor-static stop simply doesn't fire on macOS.
"""

from __future__ import annotations

import os
import platform
import subprocess
from typing import Optional


# ── Cursor position ────────────────────────────────────────────────────────────

def get_cursor_pos() -> Optional[tuple[int, int]]:
    """Return (x, y) global cursor position or None if not available."""
    system = platform.system()
    if system == "Windows":
        return _windows_cursor_pos()
    if system == "Darwin":
        return _macos_cursor_pos()
    return _linux_cursor_pos()


def _windows_cursor_pos() -> Optional[tuple[int, int]]:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None
    pt = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return (int(pt.x), int(pt.y))


def _macos_cursor_pos() -> Optional[tuple[int, int]]:
    try:
        from Quartz import CGEventCreate, CGEventGetLocation  # type: ignore
    except Exception:
        return None
    try:
        event = CGEventCreate(None)
        loc = CGEventGetLocation(event)
        return (int(loc.x), int(loc.y))
    except Exception:
        return None


def _linux_cursor_pos() -> Optional[tuple[int, int]]:
    try:
        result = subprocess.run(
            ["xdotool", "getmouselocation", "--shell"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    coords: dict[str, int] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if k in ("X", "Y"):
                try:
                    coords[k] = int(v)
                except ValueError:
                    return None
    if "X" not in coords or "Y" not in coords:
        return None
    return (coords["X"], coords["Y"])


# ── Foreground-window probe ────────────────────────────────────────────────────

def is_blender_focused() -> Optional[bool]:
    """Return True if Blender is foreground, False if not, None if unknown."""
    system = platform.system()
    if system == "Windows":
        return _windows_is_focused()
    if system == "Darwin":
        return _macos_is_focused()
    return _linux_is_focused()


def _windows_is_focused() -> Optional[bool]:
    """Compare the foreground window's PID to our own."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return bool(pid.value == os.getpid())


def _macos_is_focused() -> Optional[bool]:
    """osascript: ask System Events for the frontmost process name."""
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name.lower().startswith("blender")


def _linux_is_focused() -> Optional[bool]:
    """xdotool: query the active window's name and string-match."""
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return "blender" in result.stdout.strip().lower()
