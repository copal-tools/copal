# shell_integration.py — Explorer / Finder right-click integration.
#
# Four verbs are installed:
#   "Copal: Start Timer"        (target: folder)
#   "Copal: Stop Timer"         (target: folder)
#   "Copal: New Project Here"   (target: folder)
#   "Copal: Mark as Deliverable" (target: file — multi-select bundles via batch marker)
#
# Windows registration (HKLM, requires admin):
#   folder verbs → HKLM\Software\Classes\Directory\shell\Copal*
#                  HKLM\Software\Classes\Directory\Background\shell\Copal*  (empty space)
#   file verbs   → HKLM\Software\Classes\*\shell\Copal*    (all files; legacy menu only)
#
# macOS registration:
#   ~/Library/Services/<verb>.workflow bundles. Folder verbs use
#   Info.plist.template + document.wflow.template; file verbs use the .file
#   variants captured from an Automator-saved file-targeted Quick Action
#   (gotcha #12 — never hand-roll the plist).
#
# Each verb dispatches to `copalpm shell-trigger <verb> --folder PATH` or
# `--file PATH`. The hidden subcommand is the real implementation, so the
# registry / .workflow command strings stay stable as the underlying logic
# evolves.

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
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
        "target":  "folder",
    },
    {
        "id":      "CopalStopTimer",
        "title":   "Copal: Stop Timer",
        "trigger": "stop",
        "icon":    "copal-stop.ico",
        "target":  "folder",
    },
    {
        "id":      "CopalNewProject",
        "title":   "Copal: New Project Here",
        "trigger": "new-project",
        "icon":    "copal-new.ico",
        "target":  "folder",
    },
    {
        "id":      "CopalMarkDeliverable",
        "title":   "Copal: Mark as Deliverable",
        "trigger": "mark-deliverable",
        "icon":    "copal-deliver.ico",
        "target":  "file",
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

# Folder verbs live under HKLM (not HKCU). Win11 24H2/25H2 (build 26200+) silently
# filters per-user shell verbs added after the OS upgrade — see gotcha #10.
# File verbs use `*\shell` (the asterisk class = all file types) so the verb
# appears under any file in the legacy context menu (Shift+right-click).
_WIN_FOLDER_PARENTS = (
    r"Software\Classes\Directory\shell",
    r"Software\Classes\Directory\Background\shell",
)
_WIN_FILE_PARENTS = (
    r"Software\Classes\*\shell",
)
# Kept for backwards-compat with older imports / tests that pre-date the
# target-aware refactor. Same value as the folder parents.
_WIN_PARENTS = _WIN_FOLDER_PARENTS


def _win_parents_for(target: str) -> tuple[str, ...]:
    """Return the HKLM key prefixes a verb should be registered under.

    Folder verbs install under both the directory and its background; file
    verbs install under the all-files (`*`) class.
    """
    if target == "file":
        return _WIN_FILE_PARENTS
    return _WIN_FOLDER_PARENTS


def _all_win_parents() -> tuple[str, ...]:
    """Every HKLM parent path the verbs could be registered under (no dups)."""
    seen: list[str] = []
    for verb in VERBS:
        for parent in _win_parents_for(verb["target"]):
            if parent not in seen:
                seen.append(parent)
    return tuple(seen)


def _is_admin() -> bool:
    """True if the current process has Administrator rights on Windows."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _require_admin_or_explain(action: str) -> bool:
    """Return True if elevated; otherwise print guidance and return False."""
    if _is_admin():
        return True
    print(f"error: `copalpm shell-integration {action}` needs Administrator rights.",
          file=sys.stderr)
    print("",  file=sys.stderr)
    print("Right-click menu shortcuts must be registered system-wide on Windows —",
          file=sys.stderr)
    print("that's a Windows requirement, not a Copal one. They can't be installed",
          file=sys.stderr)
    print("as a regular user.", file=sys.stderr)
    print("",  file=sys.stderr)
    print("To proceed:", file=sys.stderr)
    print("  1. Press Win+X and pick \"Terminal (Admin)\".", file=sys.stderr)
    print("  2. Re-run the same command from that elevated terminal.", file=sys.stderr)
    return False


def _flag_for_target(target: str) -> str:
    return "--file" if target == "file" else "--folder"


def _win_command_string(
    binary: Path,
    trigger: str,
    placeholder: str,
    flag: str = "--folder",
) -> str:
    """Build the registry `command` value.

    Default flag is `--folder` for backwards-compat with the existing folder
    verbs; pass `flag="--file"` for file-targeted verbs.
    """
    return f'"{binary}" shell-trigger {trigger} {flag} "{placeholder}"'


def _install_windows() -> int:
    if not _require_admin_or_explain("install"):
        return 1

    import winreg

    binary = _copalpm_bin()
    for verb in VERBS:
        flag = _flag_for_target(verb["target"])
        for parent in _win_parents_for(verb["target"]):
            # %1 for "on a folder/file", %V for "background of a folder".
            placeholder = "%V" if "Background" in parent else "%1"
            key_path = f"{parent}\\{verb['id']}"
            with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, key_path) as k:
                winreg.SetValue(k, "", winreg.REG_SZ, verb["title"])
                icon = _asset(verb["icon"])
                if icon.exists():
                    winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, str(icon))
            cmd_path = f"{key_path}\\command"
            with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, cmd_path) as k:
                winreg.SetValue(
                    k, "", winreg.REG_SZ,
                    _win_command_string(binary, verb["trigger"], placeholder, flag),
                )
    print("Installed Copal right-click verbs to HKLM (system-wide, all users).")
    print()
    print("On Windows 11, custom verbs only appear in the legacy context menu:")
    print("  - Shift+right-click on a folder or file, OR")
    print("  - Right-click then 'Show more options' (bottom of the modern menu).")
    print()
    print("If verbs still don't appear, restart Explorer:")
    print("  taskkill /F /IM explorer.exe & start explorer.exe")
    return 0


def _uninstall_windows() -> int:
    import winreg

    # Probe HKLM first — only require admin if there's actually something
    # there to remove. Lets users clean up stale HKCU entries (from older
    # installs) without an elevation prompt.
    parents_all = _all_win_parents()

    has_hklm = False
    for parent in parents_all:
        for verb in VERBS:
            try:
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{parent}\\{verb['id']}").Close()
                has_hklm = True
                break
            except FileNotFoundError:
                continue
        if has_hklm:
            break

    if has_hklm and not _require_admin_or_explain("uninstall"):
        return 1

    removed_hklm = 0
    if has_hklm:
        for parent in parents_all:
            for verb in VERBS:
                base = f"{parent}\\{verb['id']}"
                for sub in ("command",):
                    try:
                        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}")
                    except FileNotFoundError:
                        pass
                try:
                    winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, base)
                    removed_hklm += 1
                except FileNotFoundError:
                    pass

    # Always clean up any stale HKCU keys from pre-HKLM-pivot installs.
    # Even on Win11 24H2+ where they don't show in Explorer, they're clutter.
    # HKCU writes don't need admin.
    removed_hkcu = 0
    for parent in parents_all:
        for verb in VERBS:
            base = f"{parent}\\{verb['id']}"
            for sub in ("command",):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{base}\\{sub}")
                except FileNotFoundError:
                    pass
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base)
                removed_hkcu += 1
            except FileNotFoundError:
                pass

    parts = []
    if removed_hklm:
        parts.append(f"{removed_hklm} from HKLM")
    if removed_hkcu:
        parts.append(f"{removed_hkcu} stale from HKCU")
    if not parts:
        print("No Copal verb keys found.")
    else:
        print(f"Removed {' + '.join(parts)} Copal verb key(s).")
    return 0


def _status_windows() -> int:
    import winreg

    print("Windows shell integration:")
    for verb in VERBS:
        for parent in _win_parents_for(verb["target"]):
            key_path = f"{parent}\\{verb['id']}"
            try:
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path).Close()
                state = "installed"
            except FileNotFoundError:
                state = "missing"
            print(f"  {state:>9}  HKLM\\{key_path}")
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


def _mac_template_exists(name: str) -> bool:
    return Path(str(files("copalpm") / "assets" / "macos_workflow" / name)).exists()


def _mac_template_names_for(verb: dict) -> tuple[str, str]:
    """Return (info_plist_template_name, workflow_template_name) for a verb."""
    if verb["target"] == "file":
        return ("Info.plist.file.template", "document.wflow.file.template")
    return ("Info.plist.template", "document.wflow.template")


def _mac_info_plist(verb: dict) -> str:
    info_name, _ = _mac_template_names_for(verb)
    return _mac_template(info_name).replace("__MENU_TITLE__", verb["title"])


def _mac_workflow_xml(binary: Path, verb: dict) -> str:
    # Force POSIX separators — this XML is consumed by macOS Automator, but the
    # installer may be cross-built (e.g. running tests on Windows).
    _, wflow_name = _mac_template_names_for(verb)
    flag = _flag_for_target(verb["target"])
    shell_cmd = f'"{binary.as_posix()}" shell-trigger {verb["trigger"]} {flag} "$1"'
    return _mac_template(wflow_name).replace("__COPALPM_COMMAND__", shell_cmd)


def _install_macos() -> int:
    binary = _copalpm_bin()
    _MAC_SERVICES_DIR.mkdir(parents=True, exist_ok=True)
    skipped = []
    for verb in VERBS:
        info_name, wflow_name = _mac_template_names_for(verb)
        if not (_mac_template_exists(info_name) and _mac_template_exists(wflow_name)):
            skipped.append(verb["title"])
            continue
        bundle = _mac_bundle_path(verb)
        contents = bundle / "Contents"
        contents.mkdir(parents=True, exist_ok=True)
        (contents / "Info.plist").write_text(_mac_info_plist(verb), encoding="utf-8")
        (contents / "document.wflow").write_text(
            _mac_workflow_xml(binary, verb), encoding="utf-8"
        )
    subprocess.run(
        ["/System/Library/CoreServices/pbs", "-flush"],
        check=False, capture_output=True,
    )
    installed_count = len(VERBS) - len(skipped)
    print(f"Installed {installed_count} Copal Quick Action(s).")
    print()
    print("Find them in Finder by right-clicking a project folder (or a file inside one)")
    print("→ Services or Quick Actions submenu.")
    print()
    print("If you don't see them, enable each verb in:")
    print("  System Settings → Keyboard → Keyboard Shortcuts… → Services → Files and Folders")
    print("Newly installed Services arrive unticked; Finder hides them until enabled.")
    if skipped:
        print(f"Skipped (template missing): {', '.join(skipped)}", file=sys.stderr)
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


# ── Deliverable batch marker ─────────────────────────────────────────────────
#
# Windows Explorer fires the file-targeted verb once per selected file. To
# group files selected together in one right-click into a single deliverable,
# we drop a short-lived marker (5 s TTL) at <DATA_DIR>/.deliverable-batch.json
# pointing at the deliverables[] index of the entry that's actively accepting
# additions. Subsequent invocations within the TTL append to that entry's
# `paths` list instead of creating a new entry.
#
# The cross-project guard is the `project_id` field: if a second invocation
# resolves to a different project, the marker is ignored and a new entry is
# created in the new project.

_BATCH_MARKER_NAME = ".deliverable-batch.json"
_BATCH_TTL_SECONDS = 5


def _batch_marker_path() -> Path:
    from .config import DATA_DIR
    return DATA_DIR / _BATCH_MARKER_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _read_batch_marker() -> dict | None:
    """Return the marker dict if present, well-formed, and not expired."""
    path = _batch_marker_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    pid       = data.get("project_id")
    idx       = data.get("deliverable_index")
    expires_s = data.get("expires_at")
    if not isinstance(pid, str) or not isinstance(idx, int) or not isinstance(expires_s, str):
        return None
    expires = _parse_iso(expires_s)
    if expires is None or expires <= datetime.now(timezone.utc):
        return None
    return data


def _write_batch_marker(project_id: str, deliverable_index: int) -> None:
    """Atomically write the marker, refreshing its TTL."""
    path    = _batch_marker_path()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=_BATCH_TTL_SECONDS))
    payload = {
        "project_id":        project_id,
        "deliverable_index": deliverable_index,
        "expires_at":        expires.isoformat().replace("+00:00", "Z"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=_BATCH_MARKER_NAME + ".tmp.",
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _clear_batch_marker() -> None:
    """Best-effort delete; used by tests and shouldn't raise."""
    try:
        _batch_marker_path().unlink(missing_ok=True)
    except OSError:
        pass


# ── Mark-as-deliverable handler ──────────────────────────────────────────────

def _cmd_mark_deliverable(args) -> int:
    """Handle `copalpm shell-trigger mark-deliverable --file PATH`.

    Resolves the file's owning project, then either creates a new deliverable
    entry or appends to the most-recent one (when a fresh batch marker points
    at the same project).
    """
    from .deliver_cli import _relativize, normalize_deliverables, _iso_now
    from .project_lookup import find_project_for_path
    from .project_record import load_yaml, save_yaml

    if not args.file:
        _notify("Copal", "mark-deliverable requires --file PATH", kind="error")
        return 1

    target = Path(args.file).resolve()
    if not target.exists() or not target.is_file():
        _notify("Copal", f"File not found: {target.name}", kind="error")
        return 1

    match = find_project_for_path(target)
    if match is None:
        _notify(
            "Copal",
            f"'{target.name}' is not inside a Copal project.",
            kind="error",
        )
        return 1

    yaml_path = match.project_root / "project.yaml"
    if not yaml_path.exists():
        _notify(
            "Copal",
            f"project.yaml missing for {match.project_name or match.project_id}.",
            kind="error",
        )
        return 1

    record = load_yaml(yaml_path)
    normalize_deliverables(record)
    deliverables = record.setdefault("deliverables", [])

    stored_path = _relativize(target, match.project_root)
    project_label = match.project_name or match.project_id

    marker = _read_batch_marker()
    can_append = (
        marker is not None
        and marker["project_id"] == match.project_id
        and 0 <= marker["deliverable_index"] < len(deliverables)
    )

    if can_append:
        idx = marker["deliverable_index"]
        entry = deliverables[idx]
        entry.setdefault("paths", []).append(stored_path)
        save_yaml(yaml_path, record)
        _write_batch_marker(match.project_id, idx)
        n = len(entry["paths"])
        _notify("Copal", f"Added '{target.stem}' to batch — {n} files now")
        return 0

    new_entry = {
        "name":         target.stem,
        "paths":        [stored_path],
        "type":         "draft",
        "recipient":    "internal",
        "delivered_at": _iso_now(),
        "notes":        "",
    }
    deliverables.append(new_entry)
    save_yaml(yaml_path, record)
    _write_batch_marker(match.project_id, len(deliverables) - 1)
    _notify(
        "Copal: marked as deliverable",
        f"'{target.stem}' in {project_label}",
    )
    return 0


# ── Hidden trigger handler (invoked by the OS shell verbs) ───────────────────

def cmd_shell_trigger(args) -> int:
    """Internal: invoked by the OS verbs with --folder PATH or --file PATH."""
    trigger = args.trigger

    if trigger == "mark-deliverable":
        return _cmd_mark_deliverable(args)

    from .time_cli import _find_project_id_from, _find_phase_from, _api, ServiceDownError, ApiError

    folder = Path(args.folder).resolve() if args.folder else Path.cwd()
    if not folder.exists():
        _notify("Copal", f"Folder not found: {folder}", kind="error")
        return 1

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
            resp = _api("POST", "/start", {
                "projectId":   pid,
                "description": None,
                "tool":        None,
                "phase":       phase,
            })
            stopped_prev = resp.get("stopped_prev") if isinstance(resp, dict) else None
            if stopped_prev:
                prev_pid = stopped_prev.get("project_id", "")
                mins     = int(stopped_prev.get("duration_sec", 0)) // 60
                _notify("Copal: timer stopped", f"{prev_pid} — {mins} min logged")
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
