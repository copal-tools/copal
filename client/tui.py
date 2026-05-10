import os
import sys
import getpass
import shutil
import time
import subprocess
import argparse
from datetime import datetime

from copal_core import fs, api, versioning, registry
from copal_core.config import SETTINGS, API_BASE
from copal_core.sync import SyncEngine
from copal_core import pm_hooks

# ── Colour helpers (disabled when stdout is not a tty, e.g. piped to pm-tui) ──
_COLOUR = sys.stdout.isatty()

def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text

green  = lambda t: _c("32", t)
red    = lambda t: _c("31", t)
yellow = lambda t: _c("33", t)
cyan   = lambda t: _c("36", t)
bold   = lambda t: _c("1",  t)
dim    = lambda t: _c("2",  t)


def svc_badge(s):
    if s == "ok":       return green("OK")
    if s == "degraded": return yellow("DEGRADED")
    return red((s or "?").upper())


# ── Formatting ─────────────────────────────────────────────────────────────────
def fmt_date(iso_str, short=False):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d") if short else dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str[:10]


def fmt_size(b):
    if b is None:
        return "—"
    if b == 0:
        return "0 B"
    for unit, div in [("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)]:
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return f"{b} B"


def _tw():
    return min(shutil.get_terminal_size((80, 24)).columns, 80)


def _rule(char="─"):
    print(char * _tw())


# ── Screen helpers ─────────────────────────────────────────────────────────────
def _clear():
    subprocess.run("cls" if os.name == "nt" else "clear", shell=True, capture_output=True)


def _header(subtitle="Asset Dashboard"):
    _clear()
    w = _tw()
    print("=" * w)
    print(bold(f"   COPAL-VX  |  {subtitle}"))
    print("=" * w)
    print()


def print_progress(current, total, message):
    """Text progress bar used during push/pull operations."""
    pct = (current / total * 100) if total else 0
    filled = int(30 * current // max(total, 1))
    bar = "█" * filled + "─" * (30 - filled)
    sys.stdout.write(f"\r  [{bar}] {pct:.0f}%  {message[:38]:<38}")
    sys.stdout.flush()


# ── Dashboard (main screen) ────────────────────────────────────────────────────
def show_dashboard():
    while True:
        _header("Asset Dashboard")

        # Health status row
        try:
            h = api.get_health()
            svc = h.get("services", {})
            overall = green("HEALTHY") if h.get("healthy") else red("DEGRADED")
            print(
                f"  System: {overall}"
                f"  |  API: {svc_badge(svc.get('api'))}"
                f"  DB: {svc_badge(svc.get('database'))}"
                f"  SeaweedFS: {svc_badge(svc.get('seaweedfs'))}"
            )
        except Exception:
            print(f"  System: {red('UNREACHABLE')}  —  {dim(API_BASE)} not responding")

        try:
            st = api.get_server_stats()
            print(
                f"  {st['total_projects']} project(s)"
                f"  |  {st['total_versions']} version(s)"
                f"  |  {fmt_size(st['total_storage_bytes'])} stored"
                f"  |  {st['total_unique_blobs']} unique blob(s)"
            )
        except Exception:
            pass

        print(f"  Server:  {dim(API_BASE)}")
        print()

        # Project table
        projects = []
        try:
            projects = api.list_projects()
        except Exception as e:
            print(f"  {red('Error fetching projects:')} {e}")

        _rule()
        if projects:
            hdr = f"  {'#':>3}  {'Project':<18}  {'Latest':>7}  {'Vers':>4}  {'Size':>8}  {'Last Push':<11}  Author"
            print(bold(hdr))
            _rule()
            for i, p in enumerate(projects, 1):
                name = (p["name"] or "")[:17]
                ver  = (p["latest_version"] or "—")[:7]
                vers = str(p["version_count"])
                size = fmt_size(p.get("total_storage_bytes"))
                date = fmt_date(p["last_push"], short=True) if p["last_push"] else "—"
                auth = (p["last_author"] or "—")[:12]
                print(f"  {i:>3}  {name:<18}  {ver:>7}  {vers:>4}  {size:>8}  {date:<11}  {auth}")
            _rule()
            print(f"  {len(projects)} project(s)")
        else:
            print(f"  {dim('No projects on server.')}")
            _rule()

        print()
        print(
            f"  {cyan('[1-N]')} Open  "
            f"{cyan('[P]')}ush  "
            f"{cyan('[L]')}ull  "
            f"{cyan('[R]')}efresh  "
            f"{cyan('[Q]')}uit"
        )

        choice = input("\n  > ").strip().lower()

        if choice in ("q", "quit", "exit"):
            print("Bye!")
            sys.exit()
        elif choice in ("r", ""):
            continue
        elif choice in ("p", "push"):
            do_push()
        elif choice in ("l", "pull"):
            do_pull()
        elif choice.isdigit():
            idx = int(choice) - 1
            if projects and 0 <= idx < len(projects):
                show_project(projects[idx]["name"])
            else:
                print(f"  {red('Invalid selection.')}")
                input("  Press Enter...")
        else:
            print(f"  {red(f'Unknown command: {choice!r}')}")
            input("  Press Enter...")


# ── Version diff helper ────────────────────────────────────────────────────────
def _show_diff(name, base_tag, versions):
    """Prompt for a second version and display the diff against base_tag."""
    _header(f"Diff: {name}")
    others = [v for v in versions if v != base_tag]
    if not others:
        print(f"  {yellow('Only one version — nothing to compare against.')}")
        input("  Press Enter...")
        return
    for i, v in enumerate(others[:10]):
        print(f"  {i + 1:>2}.  {v}")
    _rule()
    raw = input(f"  Compare {base_tag} against [Enter to cancel]: ").strip()
    if not raw:
        return
    if raw.isdigit():
        idx = int(raw) - 1
        if not (0 <= idx < len(others)):
            print(f"  {red('Invalid selection.')}")
            input("  Press Enter...")
            return
        other = others[idx]
    else:
        other = versioning.ensure_prefix(raw)

    # Auto-order so the diff always reads older → newer
    v1_pos = versions.index(base_tag) if base_tag in versions else -1
    v2_pos = versions.index(other) if other in versions else -1
    if v1_pos != -1 and v2_pos != -1 and v1_pos < v2_pos:
        diff_v1, diff_v2 = other, base_tag   # base_tag is newer → flip
    else:
        diff_v1, diff_v2 = base_tag, other

    _header(f"Diff: {name}  {diff_v1} → {diff_v2}")
    try:
        result = api.get_diff(name, diff_v1, diff_v2)
        if result is None:
            print(f"  {red('One or both versions not found.')}")
            input("  Press Enter...")
            return
    except Exception as ex:
        print(f"  {red('Error:')} {ex}")
        input("  Press Enter...")
        return

    added           = result.get("added", [])
    removed         = result.get("removed", [])
    changed         = result.get("changed", [])
    unchanged_count = result.get("unchanged_count", 0)

    _rule()
    if not added and not removed and not changed:
        print(f"  {green('Versions are identical.')}  ({unchanged_count} file(s) unchanged)")
    else:
        for f in sorted(removed, key=lambda x: x["path"]):
            print(f"  {red('-')} {f['path'][:54]:<54}  {fmt_size(f['size']):>9}")
        for f in sorted(added, key=lambda x: x["path"]):
            print(f"  {green('+')} {f['path'][:54]:<54}  {fmt_size(f['size']):>9}")
        for f in sorted(changed, key=lambda x: x["path"]):
            print(f"  {yellow('~')} {f['path'][:43]:<43}  {fmt_size(f['old_size']):>9} → {fmt_size(f['new_size']):>9}")
    _rule()
    parts = []
    if added:   parts.append(green(f"+{len(added)} added"))
    if removed: parts.append(red(f"-{len(removed)} removed"))
    if changed: parts.append(yellow(f"~{len(changed)} changed"))
    parts.append(dim(f"{unchanged_count} unchanged"))
    print(f"  {'  |  '.join(parts)}")
    input("\n  Press Enter to return...")


# ── Project detail ─────────────────────────────────────────────────────────────
def show_project(name):
    while True:
        _header(f"Project: {name}")

        # Fetch metadata and versions — cached for use in all choice handlers
        meta = {}
        versions = []

        try:
            meta = api.get_metadata(name)
            size_str = fmt_size(meta.get("total_size_bytes"))
            authors  = ", ".join(meta.get("authors", []))
            print(f"  Latest:   {bold(meta['latest_version'])}   Size: {size_str}   Updated: {fmt_date(meta['updated_at'], short=True)}")
            print(f"  Created:  {fmt_date(meta['created_at'], short=True)}   Authors: {authors}")
            print(f"  Message:  {dim(meta.get('message', ''))}")
            if meta.get("description"):
                print(f"  Notes:    {meta['description']}")
        except ValueError as e:
            print(f"  {yellow(str(e))}")
        except Exception as e:
            print(f"  {red('Metadata unavailable:')} {e}")

        print()

        try:
            versions = api.get_versions(name)
            _rule()
            print(f"  Versions  ({len(versions)} total)")
            _rule()
            if versions:
                for i, v in enumerate(versions[:15]):
                    suffix = cyan("  ← latest") if i == 0 else ""
                    print(f"  {v}{suffix}")
                if len(versions) > 15:
                    print(f"  {dim(f'... and {len(versions) - 15} more')}")
            else:
                print(f"  {dim('No versions yet.')}")
            _rule()
        except Exception as e:
            print(f"  {red('Could not load versions:')} {e}")

        print()
        print(
            f"  {cyan('[P]')}ush  "
            f"{cyan('[L]')}ull  "
            f"{cyan('[N]')}ame  "
            f"{cyan('[E]')}dit notes  "
            f"{cyan('[F]')}iles  "
            f"{cyan('[D]')}elete  "
            f"{cyan('[B]')}ack"
        )

        choice = input("\n  > ").strip().lower()

        if choice in ("b", "back", ""):
            return
        elif choice in ("p", "push"):
            do_push(preset_project=name)
        elif choice in ("l", "pull"):
            do_pull(preset_project=name)
        elif choice in ("n", "name", "rename"):
            new_name = confirm_rename(name)
            if new_name:
                name = new_name
        elif choice in ("e", "edit", "notes"):
            _header(f"Edit Notes: {name}")
            current = meta.get("description", "")
            print(f"  Current: {dim(current) if current else dim('(none)')}")
            print()
            new_desc = input("  New notes (blank to cancel): ").strip()
            if new_desc:
                try:
                    api.update_description(name, new_desc)
                    print(f"  {green('Saved.')}")
                except Exception as ex:
                    print(f"  {red('Failed:')} {ex}")
            else:
                print(f"  {yellow('Cancelled.')}")
            input("  Press Enter...")
        elif choice in ("f", "files"):
            if not versions:
                print(f"  {yellow('No versions yet.')}")
                input("  Press Enter...")
                continue
            _header(f"Files: {name}")
            for i, v in enumerate(versions[:10]):
                suffix = "  (latest)" if i == 0 else ""
                print(f"  {i + 1:>2}.  {v}{suffix}")
            _rule()
            raw = input(f"  Version [Enter = {versions[0]}]: ").strip()
            if not raw:
                tag = versions[0]
            elif raw.isdigit():
                idx = int(raw) - 1
                tag = versions[idx] if 0 <= idx < len(versions) else versions[0]
            else:
                tag = versioning.ensure_prefix(raw)
            while True:
                _header(f"Files: {name} @ {tag}")
                try:
                    manifest = api.get_manifest(name, tag)
                    if not manifest:
                        print(f"  {red('Version not found.')}")
                        input("  Press Enter...")
                        break
                    files = manifest.get("files", [])
                    _rule()
                    print(bold(f"  {'Path':<50}  {'Size':>9}"))
                    _rule()
                    for f in files:
                        print(f"  {f['path'][:49]:<50}  {fmt_size(f['size']):>9}")
                    _rule()
                    total = sum(f["size"] for f in files)
                    print(f"  {len(files)} file(s)   total size: {fmt_size(total)}")
                except Exception as ex:
                    print(f"  {red('Error:')} {ex}")
                    input("  Press Enter...")
                    break
                print()
                print(f"  {cyan('[D]')}iff against another version   {cyan('[Enter]')} back")
                sub = input("\n  > ").strip().lower()
                if sub in ("", "b", "back"):
                    break
                elif sub in ("d", "diff"):
                    _show_diff(name, tag, versions)
                else:
                    print(f"  {red(f'Unknown: {sub!r}')}")
                    input("  Press Enter...")
        elif choice in ("d", "delete"):
            if confirm_delete(name):
                return  # project gone; back to dashboard
        else:
            print(f"  {red(f'Unknown command: {choice!r}')}")
            input("  Press Enter...")


# ── Rename confirmation ────────────────────────────────────────────────────────
def confirm_rename(name):
    """Returns new name on success, None on cancel."""
    _header(f"Rename: {name}")
    print(f"  Current name: {bold(name)}")
    print()

    new_name = input("  New name (blank to cancel): ").strip()
    if not new_name or new_name == name:
        print(f"\n  {yellow('Cancelled.')}")
        input("  Press Enter...")
        return None

    print(f"\n  Renaming '{bold(name)}' → '{bold(new_name)}'...")
    try:
        api.rename_project(name, new_name)
        print(f"  {green('Renamed successfully.')}")
    except (ValueError, Exception) as e:
        print(f"  {red('Rename failed:')} {e}")
        input("  Press Enter...")
        return None

    input("  Press Enter to continue...")
    return new_name


# ── Delete confirmation ────────────────────────────────────────────────────────
def confirm_delete(name):
    _header(f"Delete: {name}")
    print(f"  {bold(red('WARNING:'))} Permanently deletes '{bold(name)}' and all version history.")
    print(f"  This {bold('cannot be undone')}.")
    print()

    raw = input("  Also delete orphan blobs from storage? (y/n) [n]: ").strip().lower()
    delete_orphans = raw == "y"
    print()

    confirm = input(f"  Type '{name}' to confirm: ").strip()
    if confirm != name:
        print(f"\n  {yellow('Cancelled.')} Name did not match.")
        input("  Press Enter...")
        return False

    print(f"\n  Deleting '{name}'...")
    try:
        result = api.delete_project(name, delete_orphans)
        blobs = result.get("orphan_blobs_deleted", 0)
        msg = f"  {green('Deleted.')} Project removed."
        if blobs:
            msg += f" {blobs} blob(s) cleaned from storage."
        print(msg)
    except Exception as e:
        print(f"  {red('Delete failed:')} {e}")
        input("  Press Enter...")
        return False

    input("  Press Enter to return...")
    return True


# ── Interactive Push ───────────────────────────────────────────────────────────
def do_push(preset_project=None):
    _header("Push (Upload)")

    # Source directory
    default_path = os.getcwd()
    if SETTINGS.get("default_projects_root") and os.path.exists(SETTINGS["default_projects_root"]):
        default_path = SETTINGS["default_projects_root"]

    path_input = input(f"  Source directory [{default_path}]: ").strip()
    root_dir = path_input.replace('"', '').replace("'", "") if path_input else default_path

    if not os.path.exists(root_dir):
        print(f"  {red('Error:')} Directory does not exist: {root_dir}")
        input("  Press Enter...")
        return

    local_state = fs.load_local_state(root_dir)

    # Project name
    if preset_project:
        project = preset_project
        print(f"  Project: {bold(project)}")
    else:
        default_name = (
            (local_state or {}).get("project_id")
            or os.path.basename(os.path.normpath(root_dir))
        )
        project = input(f"  Project name [{default_name}]: ").strip() or default_name

    # Scan + preview (before asking for tag/message — shows cost upfront)
    print(f"\n  Scanning: {root_dir}")
    pm_hooks.hook_pre_push(root_dir)
    local_assets = fs.scan_directory(root_dir)

    if not local_assets:
        print(f"  {yellow('No files found (or all ignored).')}")
        input("  Press Enter...")
        return

    print("  Checking server...")
    try:
        resp   = api.handshake(project, local_assets)
        needed = set(resp.get("required_files", []))
    except Exception as e:
        print(f"  {red('Error:')} {e}")
        input("  Press Enter...")
        return

    new_bytes = sum(f["size"] for f in local_assets if f["path"] in needed)
    print()
    _rule()
    print(f"  New files:  {len(needed):>4}  ({fmt_size(new_bytes)} to upload)")
    print(f"  Unchanged:  {len(local_assets) - len(needed):>4}  (already on server)")
    print(f"  Total:      {len(local_assets):>4}  files in project")
    _rule()
    print()

    if input("  Proceed with push? (Y/n): ").strip().lower() == "n":
        print("  Aborted.")
        input("  Press Enter...")
        return

    # Smart versioning
    print("  Checking remote versions...")
    try:
        existing_tags = api.get_versions(project)
    except Exception as e:
        print(f"  {red('Error:')} {e}")
        input("  Press Enter...")
        return

    default_tag = "v1.0"
    if existing_tags:
        default_tag = versioning.increment_tag(existing_tags[0])
        print(f"  Latest on server: {existing_tags[0]}")
    else:
        print(f"  {dim('New project — no remote versions found.')}")

    while True:
        tag_input = input(f"  Version tag [{default_tag}]: ").strip()
        tag = versioning.ensure_prefix(tag_input) if tag_input else default_tag
        valid, err = versioning.validate_push_tag(tag, existing_tags)
        if valid:
            break
        print(f"  {red('Error:')} {err}")

    print(f"  Tag: {bold(tag)}")

    # Ensure project exists on server
    try:
        api.ensure_project(project)
    except Exception as e:
        print(f"  {red('Error:')} Cannot confirm project: {e}")
        input("  Press Enter...")
        return

    # Commit message & author
    default_msg = f"Update {tag}"
    msg    = input(f"  Commit message [{default_msg}]: ").strip() or default_msg
    author = SETTINGS.get("default_author", getpass.getuser())

    # Upload
    if needed:
        print(f"\n  Uploading {len(needed)} new file(s)...")
        # Deduplicate by hash: two paths with identical content share one asset row
        # on the server, so only upload the bytes once — the commit maps both paths.
        seen_hashes: set = set()
        to_upload = []
        for f in local_assets:
            if f["path"] in needed and f["hash"] not in seen_hashes:
                seen_hashes.add(f["hash"])
                to_upload.append(f)
        engine    = SyncEngine(max_threads=8)
        successful = engine.execute_upload_plan(to_upload, progress_callback=print_progress)
        print(f"\n  Uploaded {len(successful)}/{len(to_upload)}")

        if len(successful) != len(to_upload):
            print(f"  {red('Some uploads failed. Aborting.')}")
            input("  Press Enter...")
            return

        print("  Confirming to database...")
        try:
            api.confirm_uploads(successful)
        except Exception as e:
            print(f"  {red('DB error:')} {e}")
            input("  Press Enter...")
            return
    else:
        print(f"  {green('All files already on server.')} Nothing to upload.")

    # Commit
    print("  Committing...")
    try:
        api.commit(project, tag, msg, author, local_assets)
        fs.save_local_state(root_dir, project, tag)
        registry.register_project(project, root_dir, tag)
        pm_hooks.hook_post_push(root_dir, project, tag)
        print(f"\n  {green('SUCCESS!')} '{project}' @ {bold(tag)} saved.")
    except Exception as e:
        print(f"  {red('Commit failed:')} {e}")

    input("\n  Press Enter to return...")


# ── Selective pull helpers ─────────────────────────────────────────────────────
def _changed_folders(diff):
    """Group changed files by immediate parent dir, return sorted list of {folder, count}."""
    from collections import Counter
    counts = Counter()
    for cat in ("added", "removed", "changed"):
        for entry in diff.get(cat, []):
            path   = entry["path"].replace("\\", "/")
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            counts[parent] += 1
    return sorted(
        [{"folder": k, "count": v} for k, v in counts.items()],
        key=lambda x: x["folder"],
    )


def _matches_prefix(path, prefix_set):
    """True if path falls under any selected folder prefix (full subtree, Option A)."""
    norm = path.replace("\\", "/")
    for prefix in prefix_set:
        if prefix == "":
            if "/" not in norm:
                return True
        elif norm.startswith(prefix + "/"):
            return True
    return False


# ── Interactive Pull ───────────────────────────────────────────────────────────
def do_pull(preset_project=None):
    _header("Pull (Restore)")

    # Project name
    if preset_project:
        project = preset_project
        print(f"  Project: {bold(project)}")
    else:
        project = input("  Project name: ").strip()
        if not project:
            return

    # Version selection
    print("  Fetching versions...")
    try:
        versions = api.get_versions(project)
    except Exception as e:
        print(f"  {red('Error:')} {e}")
        input("  Press Enter...")
        return

    if not versions:
        print(f"  {red('No versions found for this project.')}")
        input("  Press Enter...")
        return

    print()
    _rule()
    for i, v in enumerate(versions[:10]):
        suffix = "  (latest)" if i == 0 else ""
        print(f"  {i + 1:>2}.  {v}{suffix}")
    _rule()

    tag = ""
    while not tag:
        choice = input(f"  Select [1-{min(len(versions), 10)}] or type tag [latest]: ").strip().lower()
        if choice in ("", "latest", "l"):
            tag = versions[0]
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(versions):
                tag = versions[idx]
            else:
                print(f"  {red('Invalid number.')}")
        else:
            tag = versioning.ensure_prefix(choice)
            if tag not in versions:
                print(f"  {yellow(f'Warning: {tag!r} not in history. Trying anyway...')}")

    print(f"  Tag: {bold(tag)}")

    # Target directory
    default_target = os.path.join(os.getcwd(), project)
    tdir_input = input(f"  Target directory [{default_target}]: ").strip()
    target_dir = tdir_input if tdir_input else default_target

    # Conflict policy
    cfg_policy = SETTINGS.get("conflict_policy", "backup")
    _policy_num = {"backup": "1", "overwrite": "2", "skip": "3"}.get(cfg_policy, "1")
    print()
    print("  Conflict policy for changed files:")
    print("    1. Backup (rename to .bak)")
    print("    2. Overwrite")
    print("    3. Skip (keep local)")
    policy_choice = input(f"  Select [1-3] [{_policy_num}={cfg_policy}]: ").strip() or _policy_num
    policy = {"2": "overwrite", "3": "skip"}.get(policy_choice, "backup")

    # Fetch manifest
    print("\n  Fetching manifest...")
    try:
        manifest = api.get_manifest(project, tag)
        if not manifest:
            print(f"  {red('Version not found.')}")
            input("  Press Enter...")
            return
    except Exception as e:
        print(f"  {red('Error:')} {e}")
        input("  Press Enter...")
        return

    files = manifest.get("files", [])
    print(f"  Manifest: {len(files)} file(s).")

    # ── Selective pull — show changed folders if we know the local version ──────
    is_selective = False
    local_state  = fs.load_local_state(target_dir)
    local_tag    = None
    if local_state and local_state.get("project_id") == project:
        local_tag = local_state.get("last_tag")

    if local_tag and local_tag != tag:
        try:
            diff    = api.get_diff(project, local_tag, tag)
            folders = _changed_folders(diff) if diff else []
        except Exception:
            folders = []

        if folders:
            print()
            _rule()
            print(f"  Changed vs {local_tag}:")
            for i, f in enumerate(folders, 1):
                label = f["folder"] if f["folder"] else "(root)"
                print(f"  {i:>2}.  {label:<40}  {dim(str(f['count']) + ' changed')}")
            _rule()
            raw_sel = input("  Select folders [1,2,...] or Enter for full version: ").strip()
            if raw_sel:
                selected = set()
                for part in raw_sel.replace(",", " ").split():
                    if part.isdigit():
                        idx = int(part) - 1
                        if 0 <= idx < len(folders):
                            selected.add(folders[idx]["folder"])
                if selected:
                    files = [f for f in files if _matches_prefix(f["path"], selected)]
                    is_selective = True
                    print(f"  {green(f'Filtered to {len(files)} file(s).')}")

    # Plan
    print("  Analyzing filesystem...")
    engine = SyncEngine(conflict_policy=policy, max_threads=8)
    plan   = engine.generate_plan(files, target_dir)

    counts = {"DOWNLOAD": 0, "LOCAL_COPY": 0, "SKIP": 0, "BACKUP": 0}
    for task in plan:
        act = task["action"]
        if task.get("conflict_mode") == "BACKUP" or act == "BACKUP":
            counts["BACKUP"] += 1
        if act == "LOCAL_COPY":
            counts["LOCAL_COPY"] += 1
        elif act == "DOWNLOAD":
            counts["DOWNLOAD"] += 1
        elif "SKIP" in act:
            counts["SKIP"] += 1

    _rule()
    print(f"  Download:    {counts['DOWNLOAD']}")
    print(f"  Local copy:  {counts['LOCAL_COPY']}  (no bandwidth)")
    print(f"  Backup:      {counts['BACKUP']}")
    print(f"  Skip:        {counts['SKIP']}")
    _rule()

    if input("  Proceed? (Y/n): ").strip().lower() == "n":
        print("  Aborted.")
        input("  Press Enter...")
        return

    # Execute
    print("\n  Syncing...")
    t0      = time.time()
    results = engine.execute_plan(plan, progress_callback=print_progress)
    elapsed = time.time() - t0
    print(f"\n\n  Done in {elapsed:.1f}s.")
    print(f"  Success: {results['success']}  Fail: {results['fail']}  Skipped: {results['skip']}")

    if results["fail"] > 0:
        print(f"\n  {yellow('Warning:')} {results['fail']} file(s) failed to download.")
        print(f"  {dim('Local state not updated — this checkout is incomplete.')}")
    elif is_selective:
        print(f"  {dim('Partial pull — local state not updated.')}")
    else:
        fs.save_local_state(target_dir, project, tag)
        registry.register_project(project, target_dir, tag)
        pm_hooks.hook_post_pull(target_dir, project, tag)

    input("\n  Press Enter to return...")


# ── CLI (non-interactive, called by pm-tui via subprocess) ────────────────────
def push_cli(project, tag, path, message=None, author=None):
    """Non-interactive push — all params provided, exits 0 on success, 1 on failure."""
    if not os.path.exists(path):
        print(f"Error: Path does not exist: {path}", file=sys.stderr)
        sys.exit(1)

    author  = author or SETTINGS.get("default_author", getpass.getuser())
    message = message or f"Update {tag}"

    print(f"[CopalVX] Push: {project} @ {tag}")

    try:
        api.ensure_project(project)
    except Exception as e:
        print(f"Error: Cannot confirm project: {e}", file=sys.stderr)
        sys.exit(1)

    pm_hooks.hook_pre_push(path)

    print(f"Scanning: {path}")
    local_assets = fs.scan_directory(path)
    if not local_assets:
        print("Error: No files found.", file=sys.stderr)
        sys.exit(1)

    print("Handshaking with server...")
    try:
        resp  = api.handshake(project, local_assets)
        needed = set(resp.get("required_files", []))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if needed:
        print(f"Uploading {len(needed)} new files...")
        seen_hashes: set = set()
        to_upload = []
        for f in local_assets:
            if f["path"] in needed and f["hash"] not in seen_hashes:
                seen_hashes.add(f["hash"])
                to_upload.append(f)
        engine    = SyncEngine(max_threads=8)

        def _upload_progress(done, total, msg):
            print(f"[UPLOAD] {done}/{total} {msg}", flush=True)

        successful_uploads = engine.execute_upload_plan(to_upload, progress_callback=_upload_progress)
        print(f"Uploaded {len(successful_uploads)}/{len(to_upload)}")

        if len(successful_uploads) != len(to_upload):
            print("Error: Some files failed to upload.", file=sys.stderr)
            sys.exit(1)

        try:
            api.confirm_uploads(successful_uploads)
        except Exception as e:
            print(f"Error confirming uploads: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("All files already on server.")

    print("Committing...")
    try:
        api.commit(project, tag, message, author, local_assets)
        fs.save_local_state(path, project, tag)
        registry.register_project(project, path, tag)
        pm_hooks.hook_post_push(path, project, tag)
        print(f"[CopalVX] Done: {project} @ {tag}")
    except Exception as e:
        print(f"Error: Commit failed: {e}", file=sys.stderr)
        sys.exit(1)


def pull_cli(project, tag, target, policy="backup", prefixes=None):
    """Non-interactive pull — all params provided, exits 0 on success, 1 on failure."""
    print(f"[CopalVX] Pull: {project} @ {tag} -> {target}")

    print("Fetching manifest...")
    try:
        manifest = api.get_manifest(project, tag)
        if not manifest:
            print(f"Error: Version '{tag}' not found for project '{project}'.", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    files = manifest.get("files", [])

    is_selective = bool(prefixes)
    if is_selective:
        normalized = {p.rstrip("/").replace("\\", "/") for p in prefixes}
        files = [f for f in files if _matches_prefix(f["path"], normalized)]
        if not files:
            print("Error: No files match the given prefix(es).", file=sys.stderr)
            sys.exit(1)
        print(f"Filtered to {len(files)} file(s) matching prefix(es).")

    print(f"Manifest: {len(files)} files. Analyzing...")

    engine = SyncEngine(conflict_policy=policy, max_threads=8)
    plan   = engine.generate_plan(files, target)

    def _download_progress(done, total, msg):
        print(f"[DOWNLOAD] {done}/{total} {msg}", flush=True)

    results = engine.execute_plan(plan, progress_callback=_download_progress)

    print(f"Done. Success: {results['success']} | Fail: {results['fail']} | Skipped: {results['skip']}")

    if results["fail"] > 0:
        # Do NOT save state — marking an incomplete checkout as current would hide
        # missing files on every subsequent pull (they'd appear as SKIP).
        print(f"Error: {results['fail']} file(s) failed. State not saved.", file=sys.stderr)
        sys.exit(1)

    if not is_selective:
        fs.save_local_state(target, project, tag)
        registry.register_project(project, target, tag)
        pm_hooks.hook_post_pull(target, project, tag)
    print(f"[CopalVX] Done: {project} @ {tag}")


# ── First-run setup ───────────────────────────────────────────────────────────
def setup_cli():
    """Interactive setup — creates/updates ~/.copal/config.json then tests connection."""
    import json
    import requests as _req
    from pathlib import Path

    _header("Setup")

    cfg_path = Path.home() / ".copal" / "config.json"

    # Load existing config so we can show current values as defaults
    existing = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            print(f"  Updating config: {dim(str(cfg_path))}")
            print(f"  Press Enter to keep each current value.\n")
        except Exception:
            print(f"  {yellow('Existing config unreadable')} — starting fresh.\n")
    else:
        print(f"  No config found — will create {dim(str(cfg_path))}\n")

    def ask(label, key, fallback):
        current = existing.get(key, fallback)
        val = input(f"  {label:<22} [{current}]: ").strip()
        return val if val else str(current)

    def ask_int(label, key, fallback):
        while True:
            raw = ask(label, key, fallback)
            try:
                return int(raw)
            except ValueError:
                print(f"  {red('Must be a whole number.')}")

    server_ip  = ask    ("Server IP",     "server_ip",             "192.168.178.161")
    api_port   = ask_int("API port",      "api_port",              8005)
    filer_port = ask_int("Filer port",    "filer_port",            8888)
    author     = ask    ("Author",         "default_author",        getpass.getuser())
    proj_root  = ask    ("Projects root", "default_projects_root",
                         existing.get("default_projects_root") or str(Path.home() / "Projects"))
    raw_policy = ask    ("Conflict policy",    "conflict_policy",       "backup")
    conflict_policy = raw_policy if raw_policy in ("backup", "overwrite", "skip") else "backup"
    if raw_policy not in ("backup", "overwrite", "skip"):
        print(f"  {yellow('Unknown — options: backup / overwrite / skip. Saved as backup.')}")

    new_cfg = {
        "server_ip":             server_ip,
        "api_port":              api_port,
        "filer_port":            filer_port,
        "default_author":        author,
        "default_projects_root": proj_root,
        "conflict_policy":       conflict_policy,
    }

    # Preserve any extra keys the user may have set manually (e.g. client_path)
    for k, v in existing.items():
        if k not in new_cfg:
            new_cfg[k] = v

    # Test connection using the values just entered, not the stale imported config
    print()
    print("  Testing connection...")
    try:
        r   = _req.get(f"http://{server_ip}:{api_port}/health", timeout=(5, 10))
        r.raise_for_status()
        h   = r.json()
        svc = h.get("services", {})
        overall = green("HEALTHY") if h.get("healthy") else red("DEGRADED")
        print(
            f"  {overall}"
            f"  |  API: {svc_badge(svc.get('api'))}"
            f"  DB: {svc_badge(svc.get('database'))}"
            f"  SeaweedFS: {svc_badge(svc.get('seaweedfs'))}"
        )
    except Exception as e:
        print(f"  {yellow('Warning:')} Could not reach server — {e}")
        print(f"  Config will be saved anyway. Verify server_ip / api_port if unexpected.")

    # Write config
    print()
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(new_cfg, indent=4), encoding="utf-8")
        print(f"  {green('Done.')} Config written to {dim(str(cfg_path))}")
    except Exception as e:
        print(f"  {red('Error:')} Could not write config — {e}")
        sys.exit(1)

    print()
    print(f"  Run {cyan('copalvx')} to open the dashboard.")
    print()


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(prog="copalvx", add_help=False)
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("setup")

    push_p = subparsers.add_parser("push")
    push_p.add_argument("project")
    push_p.add_argument("tag")
    push_p.add_argument("path")
    push_p.add_argument("--message", "-m", default=None)
    push_p.add_argument("--author", "-a", default=None)

    pull_p = subparsers.add_parser("pull")
    pull_p.add_argument("project")
    pull_p.add_argument("tag")
    pull_p.add_argument("target")
    pull_p.add_argument("--policy", default="backup")
    pull_p.add_argument("--prefix", dest="prefixes", action="append", default=[])

    args, _ = parser.parse_known_args()

    if args.command == "setup":
        setup_cli()
    elif args.command == "push":
        push_cli(args.project, args.tag, args.path, args.message, args.author)
    elif args.command == "pull":
        pull_cli(args.project, args.tag, args.target, args.policy, args.prefixes)
    else:
        try:
            show_dashboard()
        except KeyboardInterrupt:
            print("\nExiting...")


if __name__ == "__main__":
    main()
