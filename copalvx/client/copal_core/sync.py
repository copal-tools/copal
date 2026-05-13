import os
import sys
import shutil
import time
import concurrent.futures
from . import fs
from . import transport

_verbose = sys.stdout.isatty()


class SyncAction:
    """Enumeration of possible actions to take on a file."""
    SKIP = "SKIP"                   # File is already perfect
    DOWNLOAD = "DOWNLOAD"           # File is missing, need to fetch
    LOCAL_COPY = "LOCAL_COPY"       # File exists elsewhere locally (Smart Move)
    CONFLICT_BACKUP = "BACKUP"      # File exists but differs -> Rename old, get new
    CONFLICT_OVERWRITE = "OVERWRITE"# File exists but differs -> Delete old, get new
    CONFLICT_SKIP = "CONFLICT_SKIP" # File exists but differs -> Keep local (User pref)


def _safe_root(path):
    """Resolve symlinks/junctions once so we can validate manifest paths against
    a stable, fully-resolved root."""
    return os.path.realpath(os.path.normpath(path))


class SyncEngine:
    def __init__(self, conflict_policy="backup", max_threads=8):
        self.policy = conflict_policy.lower()
        self.threads = max_threads # Store it for execute_plan

    def generate_plan(self, server_manifest_files, local_root, last_manifest_hashes=None):
        """
        Compares Server Manifest vs Local Filesystem.
        Returns a list of task dictionaries representing the 'Plan'.

        last_manifest_hashes — optional {rel_path: sha256} map from the last locally
        synced version.  When provided, conflict resolution becomes per-file smart:
          • local hash == last hash  →  file is untouched since last sync → auto-overwrite
          • local hash != last hash  →  file was edited locally            → auto-backup
        Falls back to self.policy when the map is absent.
        """
        plan = []

        # --- STEP 1: SCAN LOCAL REALITY ---
        if _verbose:
            print("🔍 SyncEngine: Scanning local drive for smart optimizations...")
        local_files = fs.scan_directory(local_root)

        # Build two indices over the local scan:
        #   local_hash_map — hash → [absolute paths]    (used for smart-move detection)
        #   path_hash_map  — rel_path → hash             (used to skip re-hashing on conflict)
        # The scan already paid the SHA-256 cost (cached via fs hash cache when
        # possible) so re-hashing in the conflict branch below would be wasted I/O.
        local_hash_map = {}
        path_hash_map = {}
        for f in local_files:
            h = f['hash']
            if h not in local_hash_map:
                local_hash_map[h] = []
            local_hash_map[h].append(f['full_local_path'])
            path_hash_map[f['path']] = h

        if _verbose:
            print(f"ℹ️  Indexed {len(local_files)} local files.")

        # Resolve symlinks/junctions once so the path-traversal guard below
        # compares against the *real* root, not a symbolic one that could
        # itself point elsewhere.
        safe_root = _safe_root(local_root)
        safe_root_prefix = safe_root + os.sep

        # --- STEP 2: ANALYZE SERVER REQUIREMENTS ---
        for asset in server_manifest_files:
            # The relative path where the file SHOULD be
            rel_path = asset['path']
            server_hash = asset['hash']
            expected_size = asset['size']
            seaweed_fid = asset['fid']

            # The absolute path on disk. Validate it stays inside ``safe_root``
            # to guard against path-traversal attacks if the server manifest is
            # ever malicious or the server is compromised (e.g. "../../../etc/passwd"
            # or an absolute path that silently overrides local_root on os.path.join).
            full_dest_path = os.path.normpath(os.path.join(local_root, rel_path))
            resolved_dest = os.path.realpath(full_dest_path)
            if not (resolved_dest == safe_root or resolved_dest.startswith(safe_root_prefix)):
                print(f"⚠️  Skipping unsafe path from server manifest: {rel_path!r}")
                continue

            # Task Object (Data needed to execute the action later)
            task = {
                "action": None,
                "rel_path": rel_path,
                "dest_path": full_dest_path,
                "fid": seaweed_fid,
                "size": expected_size,
                "hash": server_hash,
                "source_local_path": None # Used only for LOCAL_COPY
            }

            # A. Check if Destination Exists
            if os.path.exists(full_dest_path):
                # Optimization: Check size first (fast), then hash (slow)
                local_size = os.path.getsize(full_dest_path)
                local_hash = None

                if local_size == expected_size:
                    # Reuse the scan's hash where possible; only re-hash if the
                    # file landed at a path that wasn't scanned (rare — e.g.
                    # symlinks pointing outside the project).
                    norm_rel = rel_path.replace("\\", "/")
                    local_hash = path_hash_map.get(norm_rel) or fs.calculate_hash(full_dest_path)
                    if local_hash == server_hash:
                        # Case: PERFECT MATCH
                        task["action"] = SyncAction.SKIP
                        plan.append(task)
                        continue

                # Conflict: file exists locally but differs from the target version.
                if last_manifest_hashes is not None:
                    if local_hash is None:
                        norm_rel = rel_path.replace("\\", "/")
                        local_hash = path_hash_map.get(norm_rel) or fs.calculate_hash(full_dest_path)
                    norm_path = rel_path.replace("\\", "/")
                    last_hash = last_manifest_hashes.get(norm_path)
                    if last_hash is not None and local_hash == last_hash:
                        # File is identical to the last-synced version → user
                        # hasn't touched it → safe to overwrite automatically.
                        task["action"] = SyncAction.CONFLICT_OVERWRITE
                    else:
                        # File was modified locally (or is new since last sync)
                        # → preserve the user's work with a backup.
                        task["action"] = SyncAction.CONFLICT_BACKUP
                elif self.policy == "overwrite":
                    task["action"] = SyncAction.CONFLICT_OVERWRITE
                elif self.policy == "skip":
                    task["action"] = SyncAction.CONFLICT_SKIP
                else:
                    task["action"] = SyncAction.CONFLICT_BACKUP

                # If policy is Skip, we stop here.
                if task["action"] == SyncAction.CONFLICT_SKIP:
                    plan.append(task)
                    continue

                # If Backup/Overwrite, we still need to acquire the content.
                # Fall through to check if we can copy it locally...

            # B. SMART MOVE DETECTION
            # The file isn't at the destination (or we are overwriting it).
            # Do we have this data somewhere else on the disk?
            if server_hash in local_hash_map:
                # Yes! We have a matching hash.
                candidates = local_hash_map[server_hash]
                best_source = candidates[0]
                task["source_local_path"] = best_source

                if not task["action"] or task["action"] in [SyncAction.CONFLICT_BACKUP, SyncAction.CONFLICT_OVERWRITE]:
                     task["conflict_mode"] = task["action"] if task["action"] else None
                     task["action"] = SyncAction.LOCAL_COPY
            else:
                # C. STANDARD DOWNLOAD
                if not task["action"]:
                     task["action"] = SyncAction.DOWNLOAD
                else:
                     task["conflict_mode"] = task["action"]
                     task["action"] = SyncAction.DOWNLOAD

            plan.append(task)

        return plan

    def execute_task(self, task):
        """
        Executes a single task.
        (In Phase 2 we will call this from a ThreadPool)
        """
        action = task["action"]
        dest = task["dest_path"]
        rel = task["rel_path"]

        # 1. Handle Conflicts (Backup/Overwrite)
        # We check conflict_mode if it exists, or implied by existence
        conflict_mode = task.get("conflict_mode")

        if os.path.exists(dest) and action not in [SyncAction.SKIP, SyncAction.CONFLICT_SKIP]:
            if conflict_mode == SyncAction.CONFLICT_BACKUP or self.policy == "backup":
                # Nanosecond-precision suffix avoids same-second collisions when
                # two files are backed up concurrently from the thread pool.
                timestamp = time.time_ns()
                backup_path = f"{dest}.{timestamp}.bak"
                try:
                    os.rename(dest, backup_path)
                    print(f"🛡️  Backed up {rel} -> .bak")
                except OSError as e:
                    return False, f"Backup failed: {e}"

            elif conflict_mode == SyncAction.CONFLICT_OVERWRITE or self.policy == "overwrite":
                # Overwrite by leaving the existing file in place — the
                # downstream DOWNLOAD writes to a .partial file and atomically
                # replaces the target. LOCAL_COPY uses copy-tmp + os.replace,
                # which also overwrites atomically. The explicit os.remove that
                # used to live here created a non-atomic gap (file briefly
                # absent on disk) that a parallel reader could observe.
                pass

        # 2. Perform Action
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        if action == SyncAction.SKIP or action == SyncAction.CONFLICT_SKIP:
            return True, "Skipped"

        elif action == SyncAction.LOCAL_COPY:
            src = task["source_local_path"]
            tmp = dest + ".partial"
            try:
                shutil.copy2(src, tmp)
                os.replace(tmp, dest)
                return True, "Copied Locally"
            except Exception as e:
                # Best-effort partial cleanup so we never leave a half-finished
                # file sitting next to the target name.
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return False, f"Copy Failed: {e}"

        elif action == SyncAction.DOWNLOAD:
            success, msg = transport.download_file(task["fid"], dest, task["size"], task["hash"])
            if success:
                return True, "Downloaded"
            else:
                return False, f"Download Failed: {msg}"

        return False, "Unknown Action"

    def execute_plan(self, plan, progress_callback=None):
        """
        Executes the plan using parallel threads.
        :param progress_callback: A function that takes (completed_count, total_count, filename)
        """
        total_tasks = len(plan)
        completed_tasks = 0
        results = {"success": 0, "fail": 0, "skip": 0}

        active_tasks = [t for t in plan if t["action"] not in [SyncAction.SKIP, SyncAction.CONFLICT_SKIP]]
        skipped_tasks = total_tasks - len(active_tasks)

        completed_tasks += skipped_tasks
        results["skip"] += skipped_tasks

        if progress_callback:
            progress_callback(completed_tasks, total_tasks, "Skipping unchanged files...")

        # Run active tasks in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
            future_to_task = {
                executor.submit(self.execute_task, task): task
                for task in active_tasks
            }

            for future in concurrent.futures.as_completed(future_to_task):
                task = future_to_task[future]
                filename = os.path.basename(task["rel_path"])

                try:
                    success, message = future.result()

                    if success:
                        results["success"] += 1
                        log_msg = f"✅ {filename}"
                    else:
                        results["fail"] += 1
                        log_msg = f"❌ {filename} ({message})"

                except Exception as e:
                    results["fail"] += 1
                    log_msg = f"❌ {filename} (Exception: {e})"

                completed_tasks += 1
                if progress_callback:
                    progress_callback(completed_tasks, total_tasks, log_msg)

        return results
    def execute_upload_plan(self, files_to_upload, progress_callback=None):
        """
        Uploads a list of files in parallel.
        :param files_to_upload: List of dicts { 'full_local_path': ..., 'hash': ... }
        :return: List of results for DB confirmation
        """
        total = len(files_to_upload)
        completed = 0
        successful_uploads = [] # We need to return these to update the DB

        if total == 0:
            return []

        def _upload_task(asset):
            success, result = transport.upload_file(asset['full_local_path'], asset['hash'])
            return success, result, asset

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
            future_to_asset = {
                executor.submit(_upload_task, asset): asset
                for asset in files_to_upload
            }

            for future in concurrent.futures.as_completed(future_to_asset):
                success, result, asset = future.result()
                filename = os.path.basename(asset['full_local_path'])

                if success:
                    successful_uploads.append({
                        "hash": asset['hash'],
                        "size": asset['size'],
                        "fid": result
                    })
                    log_msg = f"✅ {filename}"
                else:
                    log_msg = f"❌ {filename} ({result})"

                completed += 1
                if progress_callback:
                    progress_callback(completed, total, log_msg)

        return successful_uploads
