"""Vendored CopalPM client used inside Blender's bundled Python.

This module is shipped *into* Blender's scripts/addons/copal_blender/ as part
of the addon, NOT installed via pip. It must rely only on Python stdlib +
whatever Blender's bundled Python provides (no third-party imports).

Mirrors copalpm/src/copalpm/time_cli.py:49-80 (`_api`) exactly — drift here
silently breaks the addon. Any new endpoint or schema change in copalpm's
task_tracker.py must be reflected in BOTH this file and copalpm's own
time_cli.py.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


# ── Exceptions ─────────────────────────────────────────────────────────────────

class CopalPMError(Exception):
    """Base class for copalpm client errors."""


class NotInstalledError(CopalPMError):
    """copalpm CLI not on PATH (or fallbacks), or CopalPM config.json missing."""


class ServiceDownError(CopalPMError):
    """Daemon unreachable (urllib URLError)."""


class ApiError(CopalPMError):
    """Non-2xx HTTP response from the daemon."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── Config / binary resolution ─────────────────────────────────────────────────

def _copalpm_config_path() -> Path:
    """Return the path to CopalPM's config.json on this OS.

    Duplicates copalblender/platform_paths.py:copalpm_config_path() — the
    addon can't import the host's copalblender package, so we re-derive it
    here from copalpm/src/copalpm/config.py:11-17.
    """
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    else:
        base = Path.home() / ".config"
    return base / "copalpm" / "config.json"


def _load_pm_config() -> dict:
    cfg_path = _copalpm_config_path()
    if not cfg_path.exists():
        raise NotInstalledError(f"copalpm config not found at {cfg_path}")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


_FALLBACK_PATHS_WINDOWS = [
    r"%LOCALAPPDATA%\Microsoft\WindowsApps\copalpm.exe",
    r"%LOCALAPPDATA%\uv\tools\copalpm\Scripts\copalpm.exe",
    r"%USERPROFILE%\.local\bin\copalpm.exe",
]
_FALLBACK_PATHS_UNIX = [
    "~/.local/bin/copalpm",
    "/usr/local/bin/copalpm",
    "/opt/homebrew/bin/copalpm",
]


def _resolve_copalpm(override: str | None = None) -> str:
    """Return an absolute path to the copalpm binary or raise NotInstalledError.

    Resolution order:
      1. ``override`` (typically the addon preferences "copalpm path override").
      2. ``shutil.which("copalpm")``.
      3. Hardcoded per-OS fallback list — important on macOS where Blender
         launched from Finder has a stripped PATH that excludes ~/.local/bin.
    """
    if override:
        p = Path(os.path.expandvars(os.path.expanduser(override)))
        if p.exists():
            return str(p)
    via_path = shutil.which("copalpm")
    if via_path:
        return via_path
    fallbacks = _FALLBACK_PATHS_WINDOWS if platform.system() == "Windows" else _FALLBACK_PATHS_UNIX
    for raw in fallbacks:
        candidate = Path(os.path.expandvars(os.path.expanduser(raw)))
        if candidate.exists():
            return str(candidate)
    raise NotInstalledError("copalpm not found on PATH or in fallback locations")


# ── whose (subprocess) ─────────────────────────────────────────────────────────

def whose(
    path: str,
    *,
    copalpm_path_override: str | None = None,
    timeout: float = 8.0,
) -> dict | None:
    """Resolve a filesystem path to a CopalPM project via ``copalpm whose --json``.

    Returns the parsed JSON match dict on hit, ``None`` on miss (the CLI
    prints ``null`` and exits 1). Raises ``NotInstalledError`` if copalpm
    isn't on PATH or its config is missing; ``CopalPMError`` on bad JSON.
    """
    binary = _resolve_copalpm(copalpm_path_override)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # copalpm gotcha #6: child encoding on Windows
    try:
        result = subprocess.run(
            [binary, "whose", path, "--json"],
            capture_output=True,
            timeout=timeout,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        # _resolve_copalpm picked a path that vanished between resolve and exec.
        raise NotInstalledError(f"copalpm binary disappeared: {binary}")

    stdout = (result.stdout or "").strip()
    if not stdout or stdout == "null":
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise CopalPMError(f"copalpm whose returned invalid JSON: {stdout!r}") from e


# ── HTTP daemon (mirrors copalpm/src/copalpm/time_cli.py:49-80) ────────────────

def _api(method: str, endpoint: str, body: dict | None = None, *, timeout: float = 5.0) -> dict:
    """Authenticated request to the CopalPM task-tracker daemon."""
    cfg = _load_pm_config()
    port = cfg.get("port", 5123)
    url = f"http://127.0.0.1:{port}{endpoint}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-API-Key": cfg["api_key"],
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        try:
            err = json.loads(body_text)
            msg = err.get("error") or err.get("hint") or body_text
        except Exception:
            msg = body_text
        raise ApiError(e.code, msg)
    except urllib.error.URLError as e:
        raise ServiceDownError(str(e))


def start(
    project_id: str,
    *,
    tool: str = "blender",
    phase: str | None = None,
    description: str | None = None,
) -> dict:
    return _api("POST", "/start", {
        "projectId": project_id,
        "tool": tool,
        "phase": phase,
        "description": description,
    })


def stop(*, reason: str = "manual") -> dict:
    return _api("POST", "/stop", {"reason": reason})


def state() -> dict | None:
    """Return the current session dict or ``None`` if no session is active.

    The daemon's ``/state`` endpoint returns ``null`` when there's no session;
    we surface that as Python ``None`` rather than ``{}`` so callers can use
    the natural truthiness check.
    """
    result = _api("GET", "/state")
    # _api returns {} for an empty response body; urlopen turning JSON `null`
    # into Python None is the relevant signal.
    return result if result else None


def ping() -> dict:
    return _api("POST", "/ping")


def health() -> dict:
    """Liveness probe. The ``/health`` route is no-auth in copalpm, but we
    still go through ``_api`` for code reuse — the daemon ignores the header.
    """
    return _api("GET", "/health")
