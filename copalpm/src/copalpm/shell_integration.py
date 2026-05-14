# shell_integration.py — Explorer / Finder right-click integration.
#
# Three verbs ("Copal: Start Timer", "Copal: Stop Timer", "Copal: New Project
# Here") install per-user, no-admin:
#
#   Windows: HKCU\Software\Classes\Directory\shell\Copal*           (on a folder)
#            HKCU\Software\Classes\Directory\Background\shell\Copal* (empty space)
#   macOS:   ~/Library/Services/Copal *.workflow bundles (Automator Quick Actions).
#
# Each verb dispatches to `copalpm shell-trigger {start,stop,new-project}
# --folder PATH`. That hidden subcommand is the real implementation, so the
# registry / .workflow command strings stay stable.

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from importlib.resources import files
from pathlib import Path
from typing import Literal

from .pm import _copalpm_bin


# ── Verb definitions ─────────────────────────────────────────────────────────

VERBS = [
    {
        "id":      "CopalStartTimer",
        "title":   "Copal: Start Timer",
        "trigger": "start",
        "icon":    "copal-start.ico",
    },
    {
        "id":      "CopalStopTimer",
        "title":   "Copal: Stop Timer",
        "trigger": "stop",
        "icon":    "copal-stop.ico",
    },
    {
        "id":      "CopalNewProject",
        "title":   "Copal: New Project Here",
        "trigger": "new-project",
        "icon":    "copal-new.ico",
    },
]


def _asset(name: str) -> Path:
    """Resolve an icon asset shipped with the package."""
    return Path(str(files("copalpm") / "assets" / name))


# ── Notifications ────────────────────────────────────────────────────────────

def _notify(title: str, message: str, kind: Literal["info", "error"] = "info") -> None:
    """Show a desktop notification. Best-effort — never raises."""
    system = platform.system()
    try:
        if system == "Windows":
            _notify_windows(title, message)
        elif system == "Darwin":
            _notify_macos(title, message)
        else:
            print(f"[{kind}] {title}: {message}", file=sys.stderr)
    except Exception:
        # Notifications are cosmetic. Fall back to stderr.
        print(f"[{kind}] {title}: {message}", file=sys.stderr)


def _notify_windows(title: str, message: str) -> None:
    title_esc   = title.replace("'", "''")
    message_esc = message.replace("'", "''")
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager,"
        "Windows.UI.Notifications,ContentType=WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument,"
        "Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] > $null;"
        f"$xml = \"<toast><visual><binding template=`\"ToastGeneric`\">"
        f"<text>{title_esc}</text><text>{message_esc}</text>"
        "</binding></visual></toast>\";"
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$doc.LoadXml($xml);"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc);"
        "[Windows.UI.Notifications.ToastNotificationManager]"
        "::CreateToastNotifier('CopalPM').Show($toast);"
    )
    subprocess.run(
        ["powershell", "-WindowStyle", "Hidden", "-NoProfile", "-Command", ps],
        check=False, capture_output=True, timeout=5,
    )


def _notify_macos(title: str, message: str) -> None:
    title_esc   = title.replace('"', '\\"')
    message_esc = message.replace('"', '\\"')
    script = f'display notification "{message_esc}" with title "{title_esc}"'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=5)


# ── Windows: install / uninstall / status ─────────────────────────────────────

_WIN_PARENTS = (
    r"Software\Classes\Directory\shell",
    r"Software\Classes\Directory\Background\shell",
)


def _win_command_string(binary: Path, trigger: str, folder_placeholder: str) -> str:
    return f'"{binary}" shell-trigger {trigger} --folder "{folder_placeholder}"'


def _install_windows() -> int:
    import winreg

    binary = _copalpm_bin()
    for parent in _WIN_PARENTS:
        # %1 for "on a folder", %V for "background of a folder"
        placeholder = "%V" if "Background" in parent else "%1"
        for verb in VERBS:
            key_path = f"{parent}\\{verb['id']}"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                winreg.SetValue(k, "", winreg.REG_SZ, verb["title"])
                icon = _asset(verb["icon"])
                if icon.exists():
                    winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, str(icon))
            cmd_path = f"{key_path}\\command"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cmd_path) as k:
                winreg.SetValue(
                    k, "", winreg.REG_SZ,
                    _win_command_string(binary, verb["trigger"], placeholder),
                )
    print("Installed Copal right-click verbs (Windows Explorer).")
    print("If Explorer doesn't show them, sign out / in or restart explorer.exe.")
    return 0


def _uninstall_windows() -> int:
    import winreg

    removed = 0
    for parent in _WIN_PARENTS:
        for verb in VERBS:
            base = f"{parent}\\{verb['id']}"
            # Delete command subkey first; HKCU DeleteKey requires no subkeys.
            for sub in ("command",):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{base}\\{sub}")
                except FileNotFoundError:
                    pass
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base)
                removed += 1
            except FileNotFoundError:
                pass
    print(f"Removed {removed} Copal verb key(s) from HKCU.")
    return 0


def _status_windows() -> int:
    import winreg

    print("Windows shell integration:")
    for parent in _WIN_PARENTS:
        for verb in VERBS:
            key_path = f"{parent}\\{verb['id']}"
            try:
                winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path).Close()
                state = "installed"
            except FileNotFoundError:
                state = "missing"
            print(f"  {state:>9}  HKCU\\{key_path}")
    return 0


# ── macOS: install / uninstall / status ──────────────────────────────────────

_MAC_SERVICES_DIR = Path.home() / "Library" / "Services"


def _mac_bundle_path(verb: dict) -> Path:
    return _MAC_SERVICES_DIR / f"{verb['title'].replace(':', '')}.workflow"


def _mac_template(name: str) -> str:
    """Read a shipped macOS workflow template (XML plist with placeholders).

    Templates were captured from a real Automator-generated Quick Action — the
    structure is non-negotiable (AMWorkflowServiceRunner aborts at runtime if
    workflowMetaData / arguments / etc. don't match). Don't edit by hand.
    """
    return (Path(str(files("copalpm") / "assets" / "macos_workflow" / name))).read_text(encoding="utf-8")


def _mac_info_plist(verb: dict) -> str:
    return _mac_template("Info.plist.template").replace("__MENU_TITLE__", verb["title"])


def _mac_workflow_xml(binary: Path, trigger: str) -> str:
    # Force POSIX separators — this XML is consumed by macOS Automator, but the
    # installer may be cross-built (e.g. running tests on Windows).
    shell_cmd = f'"{binary.as_posix()}" shell-trigger {trigger} --folder "$1"'
    return _mac_template("document.wflow.template").replace("__COPALPM_COMMAND__", shell_cmd)


def _install_macos() -> int:
    binary = _copalpm_bin()
    _MAC_SERVICES_DIR.mkdir(parents=True, exist_ok=True)
    for verb in VERBS:
        bundle = _mac_bundle_path(verb)
        contents = bundle / "Contents"
        contents.mkdir(parents=True, exist_ok=True)
        (contents / "Info.plist").write_text(_mac_info_plist(verb), encoding="utf-8")
        (contents / "document.wflow").write_text(
            _mac_workflow_xml(binary, verb["trigger"]), encoding="utf-8"
        )
    subprocess.run(
        ["/System/Library/CoreServices/pbs", "-flush"],
        check=False, capture_output=True,
    )
    print("Installed Copal Quick Actions. They appear in Finder under Services.")
    return 0


def _uninstall_macos() -> int:
    removed = 0
    for verb in VERBS:
        bundle = _mac_bundle_path(verb)
        if bundle.exists():
            shutil.rmtree(bundle)
            removed += 1
    subprocess.run(
        ["/System/Library/CoreServices/pbs", "-flush"],
        check=False, capture_output=True,
    )
    print(f"Removed {removed} Copal Quick Action(s).")
    return 0


def _status_macos() -> int:
    print("macOS shell integration:")
    for verb in VERBS:
        bundle = _mac_bundle_path(verb)
        state = "installed" if bundle.exists() else "missing"
        print(f"  {state:>9}  {bundle}")
    return 0


# ── User-facing commands ─────────────────────────────────────────────────────

def cmd_shell_install(args) -> int:
    system = platform.system()
    if system == "Windows":
        return _install_windows()
    if system == "Darwin":
        return _install_macos()
    print("error: shell-integration is only supported on Windows and macOS.", file=sys.stderr)
    return 1


def cmd_shell_uninstall(args) -> int:
    system = platform.system()
    if system == "Windows":
        return _uninstall_windows()
    if system == "Darwin":
        return _uninstall_macos()
    print("error: shell-integration is only supported on Windows and macOS.", file=sys.stderr)
    return 1


def cmd_shell_status(args) -> int:
    system = platform.system()
    if system == "Windows":
        return _status_windows()
    if system == "Darwin":
        return _status_macos()
    print("error: shell-integration is only supported on Windows and macOS.", file=sys.stderr)
    return 1


# ── TUI spawn helper ─────────────────────────────────────────────────────────

def _spawn_tui_in_terminal(folder: Path) -> None:
    """Open a new terminal window running `copalpm tui --screen init --dir folder`.

    The TUI needs a real TTY of its own — running it as a detached child of
    Finder/Explorer (or piggy-backing on the invoking shell's TTY) makes
    Textual either fail to attach or fight the parent for keyboard input.
    """
    binary = _copalpm_bin()

    if platform.system() == "Windows":
        # Prefer Windows Terminal if installed — it gives the TUI a proper
        # ConPTY pseudo-terminal. Fall back to legacy console via cmd.
        wt = shutil.which("wt.exe")
        if wt:
            cmd = [wt, "new-tab", "--title", "Copal: New Project",
                   str(binary), "tui", "--screen", "init", "--dir", str(folder)]
            subprocess.Popen(cmd, close_fds=True)
            return
        # Legacy: spawn cmd.exe with its own console window.
        CREATE_NEW_CONSOLE = 0x00000010
        cmd = [str(binary), "tui", "--screen", "init", "--dir", str(folder)]
        subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE, close_fds=True)
        return

    # macOS — drive Terminal.app via osascript so the TUI gets a fresh window.
    parts = [shlex.quote(str(binary)), "tui", "--screen", "init",
             "--dir", shlex.quote(str(folder))]
    shell_cmd = " ".join(parts)
    # Escape for AppleScript string literal (backslashes first, then quotes).
    shell_cmd_as = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
    applescript = (
        'tell application "Terminal" to activate\n'
        f'tell application "Terminal" to do script "{shell_cmd_as}"'
    )
    subprocess.Popen(
        ["osascript", "-e", applescript],
        close_fds=True, start_new_session=True,
    )


# ── Hidden trigger handler (invoked by the OS shell verbs) ───────────────────

def cmd_shell_trigger(args) -> int:
    """Internal: invoked by the OS verbs with --folder PATH."""
    from .time_cli import _find_project_id_from, _find_phase_from, _api, ServiceDownError, ApiError
    from argparse import Namespace

    folder = Path(args.folder).resolve() if args.folder else Path.cwd()
    if not folder.exists():
        _notify("Copal", f"Folder not found: {folder}", kind="error")
        return 1

    trigger = args.trigger

    if trigger == "new-project":
        # The TUI needs its own controlling terminal — sharing the parent's
        # TTY makes Textual fight the parent shell for stdin (keystrokes go
        # to both). Spawn a fresh terminal window per platform.
        try:
            _spawn_tui_in_terminal(folder)
        except OSError as e:
            _notify("Copal", f"Could not launch TUI: {e}", kind="error")
            return 1
        return 0

    # start / stop need the task-tracker service running.
    try:
        if trigger == "start":
            pid = _find_project_id_from(folder)
            if not pid:
                _notify("Copal", f"No project.yaml found at or above {folder.name}.", kind="error")
                return 1
            phase = _find_phase_from(folder)
            _api("POST", "/start", {
                "projectId":   pid,
                "description": None,
                "tool":        None,
                "phase":       phase,
            })
            _notify("Copal: timer started", f"{pid}" + (f"  ({phase})" if phase else ""))
            return 0

        if trigger == "stop":
            resp = _api("POST", "/stop", {"reason": "manual"})
            if resp.get("stopped"):
                dur = resp.get("duration_sec") or 0
                mins = dur // 60
                _notify("Copal: timer stopped", f"{mins} min logged")
            else:
                _notify("Copal", "No active session.")
            return 0

    except ServiceDownError:
        _notify(
            "Copal: service not running",
            "Run `copalpm service install` from a terminal.",
            kind="error",
        )
        return 1
    except ApiError as e:
        _notify("Copal", f"API error: {e.message}", kind="error")
        return 1

    _notify("Copal", f"Unknown trigger: {trigger}", kind="error")
    return 1
