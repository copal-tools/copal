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


def _mac_info_plist(verb: dict) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '  <key>NSServices</key>\n'
        '  <array>\n'
        '    <dict>\n'
        '      <key>NSMenuItem</key>\n'
        '      <dict>\n'
        '        <key>default</key>\n'
        f'        <string>{verb["title"]}</string>\n'
        '      </dict>\n'
        '      <key>NSMessage</key>\n'
        '      <string>runWorkflowAsService</string>\n'
        '      <key>NSSendFileTypes</key>\n'
        '      <array>\n'
        '        <string>public.folder</string>\n'
        '      </array>\n'
        '    </dict>\n'
        '  </array>\n'
        '</dict>\n'
        '</plist>\n'
    )


def _mac_workflow_xml(binary: Path, trigger: str) -> str:
    # Force POSIX separators — this XML is consumed by macOS Automator, but the
    # installer may be cross-built (e.g. running tests on Windows).
    shell = f'"{binary.as_posix()}" shell-trigger {trigger} --folder "$1"\n'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '  <key>AMApplication</key><string>Automator</string>\n'
        '  <key>AMCanShowSelectedItemsWhenRun</key><false/>\n'
        '  <key>AMCanShowWhenRun</key><true/>\n'
        '  <key>AMDockBadgeLabel</key><string></string>\n'
        '  <key>AMDockBadgeStyle</key><string>badge</string>\n'
        '  <key>AMName</key><string>Copal</string>\n'
        '  <key>AMRootElement</key>\n'
        '  <dict>\n'
        '    <key>actions</key>\n'
        '    <array>\n'
        '      <dict>\n'
        '        <key>action</key>\n'
        '        <dict>\n'
        '          <key>AMActionVersion</key><string>2.0.3</string>\n'
        '          <key>AMApplication</key><array><string>Automator</string></array>\n'
        '          <key>AMParameterProperties</key>\n'
        '          <dict>\n'
        '            <key>COMMAND_STRING</key><dict/>\n'
        '            <key>CheckedForUserDefaultShell</key><dict/>\n'
        '            <key>inputMethod</key><dict/>\n'
        '            <key>shell</key><dict/>\n'
        '            <key>source</key><dict/>\n'
        '          </dict>\n'
        '          <key>AMProvides</key>\n'
        '          <dict>\n'
        '            <key>Container</key><string>List</string>\n'
        '            <key>Types</key><array><string>com.apple.cocoa.string</string></array>\n'
        '          </dict>\n'
        '          <key>ActionBundlePath</key>\n'
        '          <string>/System/Library/Automator/Run Shell Script.action</string>\n'
        '          <key>ActionName</key><string>Run Shell Script</string>\n'
        '          <key>ActionParameters</key>\n'
        '          <dict>\n'
        f'            <key>COMMAND_STRING</key><string>{shell}</string>\n'
        '            <key>CheckedForUserDefaultShell</key><true/>\n'
        '            <key>inputMethod</key><integer>1</integer>\n'
        '            <key>shell</key><string>/bin/bash</string>\n'
        '            <key>source</key><string></string>\n'
        '          </dict>\n'
        '          <key>BundleIdentifier</key>\n'
        '          <string>com.apple.RunShellScript</string>\n'
        '          <key>CFBundleVersion</key><string>2.0.3</string>\n'
        '          <key>CanShowSelectedItemsWhenRun</key><false/>\n'
        '          <key>CanShowWhenRun</key><true/>\n'
        '          <key>Category</key><array><string>AMCategoryUtilities</string></array>\n'
        '          <key>Class Name</key><string>RunShellScriptAction</string>\n'
        '          <key>InputUUID</key><string>1B3F4F1F-0000-0000-0000-000000000001</string>\n'
        '          <key>Keywords</key><array><string>Shell</string></array>\n'
        '          <key>OutputUUID</key><string>1B3F4F1F-0000-0000-0000-000000000002</string>\n'
        '          <key>UUID</key><string>1B3F4F1F-0000-0000-0000-000000000003</string>\n'
        '          <key>UnlocalizedApplications</key><array><string>Automator</string></array>\n'
        '          <key>arguments</key><dict/>\n'
        '          <key>isViewVisible</key><true/>\n'
        '          <key>location</key><string>309.000000:316.000000</string>\n'
        '          <key>nibPath</key>\n'
        '          <string>/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib</string>\n'
        '        </dict>\n'
        '        <key>isViewVisible</key><true/>\n'
        '      </dict>\n'
        '    </array>\n'
        '    <key>connectors</key><dict/>\n'
        '    <key>workflowMetaData</key>\n'
        '    <dict>\n'
        '      <key>serviceInputTypeIdentifier</key>\n'
        '      <string>com.apple.Automator.fileSystemObject.folder</string>\n'
        '      <key>serviceOutputTypeIdentifier</key>\n'
        '      <string>com.apple.Automator.nothing</string>\n'
        '      <key>serviceProcessesInput</key><integer>0</integer>\n'
        '      <key>workflowTypeIdentifier</key>\n'
        '      <string>com.apple.Automator.servicesMenu</string>\n'
        '    </dict>\n'
        '  </dict>\n'
        '</dict>\n'
        '</plist>\n'
    )


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
        # Spawn `copalpm tui --screen init --dir <folder>` detached so the
        # Explorer/Finder context menu returns immediately.
        binary = _copalpm_bin()
        cmd = [str(binary), "tui", "--screen", "init", "--dir", str(folder)]
        kwargs = {}
        if platform.system() == "Windows":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            kwargs["close_fds"] = True
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen(cmd, **kwargs)
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
