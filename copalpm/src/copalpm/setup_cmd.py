# setup_cmd.py — `copalpm setup` and `copalpm teardown`.
#
# Umbrella commands that wrap the per-component installers (service +
# shell-integration) so a new user can get to a working state with one
# invocation. The granular `service install` / `shell-integration install`
# commands stay for advanced use.
#
# Design choices:
#   - Pre-flight admin check on Windows. We bail before touching anything
#     if elevation is missing — better than installing the service then
#     failing on shell-integration with a UAC prompt the user can't approve.
#   - Idempotent. Each step probes current state and skips if already done.
#   - Best-effort NSSM auto-install via winget on Windows. Skip with
#     `--skip-nssm` if you've installed it some other way.
#   - Granular skip flags so a re-run after partial failure can target the
#     remaining work.
#   - Always prints a final summary so the user knows exactly what state
#     things are in.

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .config import DATA_DIR


# ── Status probes ─────────────────────────────────────────────────────────────

def _service_installed_and_running() -> bool:
    """True if config.json exists AND the daemon is reachable on its port."""
    cfg_path = DATA_DIR / "config.json"
    if not cfg_path.exists():
        return False
    try:
        from .time_cli import _api, ServiceDownError, ApiError
        try:
            _api("GET", "/state")
            return True
        except (ServiceDownError, ApiError):
            return False
    except Exception:
        return False


def _shell_integration_installed() -> bool:
    """True if at least one Copal verb is in the OS shell registration."""
    system = platform.system()
    if system == "Windows":
        try:
            import winreg
            from .shell_integration import _WIN_PARENTS, VERBS
            for parent in _WIN_PARENTS:
                for verb in VERBS:
                    try:
                        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                       f"{parent}\\{verb['id']}").Close()
                        return True
                    except FileNotFoundError:
                        continue
            return False
        except Exception:
            return False
    if system == "Darwin":
        from .shell_integration import _mac_bundle_path, VERBS
        return any(_mac_bundle_path(v).exists() for v in VERBS)
    return False


# ── NSSM bootstrapping (Windows) ──────────────────────────────────────────────

def _nssm_present() -> bool:
    if shutil.which("nssm"):
        return True
    return Path(r"C:\nssm-2.24\win64\nssm.exe").exists()


def _try_install_nssm() -> bool:
    """Best-effort: install NSSM via winget. Returns True if NSSM is present after."""
    if _nssm_present():
        return True
    if not shutil.which("winget"):
        print("  winget not found — install NSSM manually (https://nssm.cc) or via Chocolatey.")
        return False
    print("  winget install NSSM.NSSM …")
    try:
        result = subprocess.run(
            ["winget", "install", "--silent", "--accept-package-agreements",
             "--accept-source-agreements", "NSSM.NSSM"],
            check=False, capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            print(f"  winget install failed (exit {result.returncode}). "
                  "Output:\n" + (result.stderr or result.stdout)[:500])
            return False
    except Exception as e:
        print(f"  winget install raised: {e}")
        return False
    # NSSM installed by winget may not be on PATH for the current session.
    if not _nssm_present():
        print("  NSSM installed but not yet on PATH for this terminal.")
        print("  Open a NEW terminal and re-run `copalpm setup` to finish.")
        return False
    return True


# ── Step runners ──────────────────────────────────────────────────────────────

def _do_service_install() -> tuple[bool, str]:
    """Install/start the task-tracker service. Returns (ok, status_msg)."""
    if _service_installed_and_running():
        return True, "already installed and running"

    if platform.system() == "Windows":
        if not _nssm_present():
            print("  NSSM not found — attempting winget install:")
            if not _try_install_nssm():
                return False, "NSSM unavailable; service install skipped"

    from .pm import cmd_install_service
    try:
        cmd_install_service()
    except SystemExit as e:
        if e.code not in (0, None):
            return False, f"cmd_install_service exited {e.code}"
    except Exception as e:
        return False, f"unexpected error: {e}"

    if _service_installed_and_running():
        return True, "installed"
    return False, "install ran but daemon is not reachable"


def _do_shell_install() -> tuple[bool, str]:
    """Install OS shell verbs. Returns (ok, status_msg)."""
    from .shell_integration import cmd_shell_install
    rc = cmd_shell_install(None)
    if rc != 0:
        return False, f"shell-integration install returned {rc}"
    if _shell_integration_installed():
        return True, "installed"
    return False, "install reported success but no verbs found"


def _do_service_uninstall() -> tuple[bool, str]:
    if not (DATA_DIR / "config.json").exists() and not _service_installed_and_running():
        return True, "not installed"
    from .pm import cmd_uninstall_service
    try:
        cmd_uninstall_service()
    except SystemExit as e:
        if e.code not in (0, None):
            return False, f"exited {e.code}"
    except Exception as e:
        return False, f"unexpected error: {e}"
    return True, "removed"


def _do_shell_uninstall() -> tuple[bool, str]:
    from .shell_integration import cmd_shell_uninstall
    rc = cmd_shell_uninstall(None)
    if rc != 0:
        return False, f"returned {rc}"
    return True, "removed"


# ── Top-level commands ────────────────────────────────────────────────────────

def cmd_setup(args) -> int:
    """End-to-end install: task-tracker service + OS shell integration."""
    skip_service = getattr(args, "skip_service", False) or getattr(args, "shell_only", False)
    skip_shell   = getattr(args, "skip_shell", False) or getattr(args, "service_only", False)

    # Preflight: on Windows, check admin once for everything that needs it.
    # Done before the banner so non-admin users see a clean error, not noise.
    if platform.system() == "Windows":
        from .shell_integration import _is_admin
        needs_admin = (not skip_service) or (not skip_shell)
        if needs_admin and not _is_admin():
            print("error: setup needs Administrator rights on Windows.", file=sys.stderr)
            print(file=sys.stderr)
            print("Required for:", file=sys.stderr)
            if not skip_service:
                print("  - Installing the task-tracker service (NSSM)", file=sys.stderr)
            if not skip_shell:
                print("  - Installing right-click verbs (HKLM)", file=sys.stderr)
            print(file=sys.stderr)
            print("Run from an elevated terminal:", file=sys.stderr)
            print("  Win+X -> Terminal (Admin), then re-run `copalpm setup`.", file=sys.stderr)
            return 1

    print("Copal setup")
    print("===========")
    print()

    results: list[tuple[str, bool, str]] = []

    if not skip_service:
        print("[1/2] Background service")
        ok, msg = _do_service_install()
        results.append(("Background service", ok, msg))
        print(f"  -> {msg}")
        print()

    if not skip_shell:
        idx = "2/2" if not skip_service else "1/1"
        print(f"[{idx}] Shell integration")
        ok, msg = _do_shell_install()
        results.append(("Shell integration", ok, msg))
        print(f"  -> {msg}")
        print()

    # Summary
    print("=" * 32)
    all_ok = all(ok for _, ok, _ in results)
    for name, ok, msg in results:
        marker = "OK" if ok else "FAILED"
        print(f"  [{marker}]  {name}: {msg}")
    print()

    if all_ok:
        print("Setup complete. Try:")
        if platform.system() == "Windows":
            print("  - Shift+right-click on a project folder -> Copal: Start Timer")
        else:
            print("  - Right-click on a project folder -> Quick Actions -> Copal: Start Timer")
        print("  - copalpm time status")
        print("  - copalpm                    # launch TUI")
        return 0
    print("Setup finished with errors. Re-run after fixing the issues above.")
    return 1


def cmd_teardown(args) -> int:
    """Reverse of setup: remove the service AND the OS shell verbs."""
    skip_service = getattr(args, "skip_service", False) or getattr(args, "shell_only", False)
    skip_shell   = getattr(args, "skip_shell", False) or getattr(args, "service_only", False)

    # Preflight admin (matches setup's logic — only flag what we'll actually do).
    if platform.system() == "Windows":
        from .shell_integration import _is_admin
        # Service uninstall always needs admin on Windows.
        # Shell-integration uninstall only needs admin if HKLM has keys.
        needs_admin = False
        if not skip_service and (DATA_DIR / "config.json").exists():
            needs_admin = True
        if not skip_shell and _shell_integration_installed():
            needs_admin = True
        if needs_admin and not _is_admin():
            print("error: teardown needs Administrator rights on Windows.", file=sys.stderr)
            print("Run from an elevated terminal: Win+X -> Terminal (Admin).", file=sys.stderr)
            return 1

    print("Copal teardown")
    print("==============")
    print()

    results: list[tuple[str, bool, str]] = []

    # Reverse order — remove shell integration first (so users don't get a
    # broken right-click verb that points at a missing service mid-teardown).
    if not skip_shell:
        print("[1/2] Shell integration")
        ok, msg = _do_shell_uninstall()
        results.append(("Shell integration", ok, msg))
        print(f"  -> {msg}")
        print()

    if not skip_service:
        idx = "2/2" if not skip_shell else "1/1"
        print(f"[{idx}] Background service")
        ok, msg = _do_service_uninstall()
        results.append(("Background service", ok, msg))
        print(f"  -> {msg}")
        print()

    print("=" * 32)
    all_ok = all(ok for _, ok, _ in results)
    for name, ok, msg in results:
        marker = "OK" if ok else "FAILED"
        print(f"  [{marker}]  {name}: {msg}")
    print()

    if all_ok:
        print("Teardown complete. User data preserved at:")
        print(f"  {DATA_DIR}")
        return 0
    return 1
