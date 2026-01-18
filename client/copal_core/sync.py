import os
import shutil
import time
from . import fs
from . import transport
import concurrent.futures

class SyncAction:
    """Enumeration of possible actions to take on a file."""
    SKIP = "SKIP"                   # File is already perfect
    DOWNLOAD = "DOWNLOAD"           # File is missing, need to fetch
    LOCAL_COPY = "LOCAL_COPY"       # File exists elsewhere locally (Smart Move)
    CONFLICT_BACKUP = "BACKUP"      # File exists but differs -> Rename old, get new
    CONFLICT_OVERWRITE = "OVERWRITE"# File exists but differs -> Delete old, get new
    CONFLICT_SKIP = "CONFLICT_SKIP" # File exists but differs -> Keep local (User pref)

class SyncEngine:
    def __init__(self, conflict_policy="backup", max_threads=8):
        self.policy = conflict_policy.lower()
        self.threads = max_threads # Store it for execute_plan

    def generate_plan(self, server_manifest_files, local_root):
        """
        Compares Server Manifest vs Local Filesystem.
        Returns a list of task dictionaries representing the 'Plan'.
        """
        plan = []
        
        # --- STEP 1: SCAN LOCAL REALITY ---
        print("🔍 SyncEngine: Scanning local drive for smart optimizations...")
        local_files = fs.scan_directory(local_root)
        
        # Build a "Hash Map" to find files regardless of their name/location.
        # Format: { "sha256_hash": ["path/to/file1", "path/to/file2"] }
        local_hash_map = {}
        for f in local_files:
            h = f['hash']
            if h not in local_hash_map:
                local_hash_map[h] = []
            local_hash_map[h].append(f['full_local_path'])

        print(f"ℹ️  Indexed {len(local_files)} local files.")

        # --- STEP 2: ANALYZE SERVER REQUIREMENTS ---
        for asset in server_manifest_files:
            # The relative path where the file SHOULD be
            rel_path = asset['path']
            server_hash = asset['hash']
            expected_size = asset['size']
            seaweed_fid = asset['fid']
            
            # The absolute path on disk
            full_dest_path = os.path.join(local_root, rel_path)
            
            # Task Object (Data needed to execute the action later)
            task = {
                "action": None,
                "rel_path": rel_path,
                "dest_path": full_dest_path,
                "fid": seaweed_fid,
                "size": expected_size,
                "source_local_path": None # Used only for LOCAL_COPY
            }

            # A. Check if Destination Exists
            if os.path.exists(full_dest_path):
                # Optimization: Check size first (fast), then hash (slow)
                local_size = os.path.getsize(full_dest_path)
                
                if local_size == expected_size:
                    local_hash = fs.calculate_hash(full_dest_path)
                    if local_hash == server_hash:
                        # Case: PERFECT MATCH
                        task["action"] = SyncAction.SKIP
                        plan.append(task)
                        continue
                
                # If we get here, it's a CONFLICT (Size or Hash mismatch)
                if self.policy == "overwrite":
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
                
                # Pick the first candidate
                # (Ideally, pick one that isn't the destination itself, but our logic handles that)
                best_source = candidates[0]
                
                task["source_local_path"] = best_source
                
                # If we flagged it as BACKUP earlier, keep that flag? 
                # No, the "Action" is how we acquire the file. 
                # But we must handle the backup rename BEFORE the copy.
                # Let's simplify: The executor will handle the backup logic based on existence.
                # We just need to switch DOWNLOAD -> LOCAL_COPY
                if not task["action"] or task["action"] in [SyncAction.CONFLICT_BACKUP, SyncAction.CONFLICT_OVERWRITE]:
                     # It was a download/overwrite, now it's a copy
                     # We store the "Conflict Mode" in a separate field to keep logic clean
                     task["conflict_mode"] = task["action"] if task["action"] else None
                     task["action"] = SyncAction.LOCAL_COPY
            else:
                # C. STANDARD DOWNLOAD
                if not task["action"]: # If not already set to Backup/Overwrite
                     task["action"] = SyncAction.DOWNLOAD
                else:
                     # It was Backup/Overwrite, so the acquisition method is Download
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
                timestamp = int(time.time())
                backup_path = f"{dest}.{timestamp}.bak"
                try:
                    os.rename(dest, backup_path)
                    print(f"🛡️  Backed up {rel} -> .bak")
                except OSError as e:
                    return False, f"Backup failed: {e}"
            
            elif conflict_mode == SyncAction.CONFLICT_OVERWRITE or self.policy == "overwrite":
                try:
                    os.remove(dest)
                except OSError as e:
                    return False, f"Overwrite delete failed: {e}"

        # 2. Perform Action
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        
        if action == SyncAction.SKIP or action == SyncAction.CONFLICT_SKIP:
            return True, "Skipped"
            
        elif action == SyncAction.LOCAL_COPY:
            src = task["source_local_path"]
            try:
                shutil.copy2(src, dest)
                return True, "Copied Locally"
            except Exception as e:
                return False, f"Copy Failed: {e}"
                
        elif action == SyncAction.DOWNLOAD:
            success = transport.download_file(task["fid"], dest, task["size"])
            if success:
                return True, "Downloaded"
            else:
                return False, "Download Failed"
                
        return False, "Unknown Action"
    
    def execute_plan(self, plan, progress_callback=None):
        """
        Executes the plan using parallel threads.
        :param progress_callback: A function that takes (completed_count, total_count, filename)
        """
        total_tasks = len(plan)
        completed_tasks = 0
        results = {"success": 0, "fail": 0, "skip": 0}
        
        # We filter out 'SKIP' actions from the thread pool to save overhead,
        # but we count them towards progress.
        active_tasks = [t for t in plan if t["action"] not in [SyncAction.SKIP, SyncAction.CONFLICT_SKIP]]
        skipped_tasks = total_tasks - len(active_tasks)
        
        # Mark skipped as done immediately
        completed_tasks += skipped_tasks
        results["skip"] += skipped_tasks
        
        if progress_callback:
            progress_callback(completed_tasks, total_tasks, "Skipping unchanged files...")

        # Run active tasks in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
            # Map each future to its task so we know which file it is
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

        # Wrapper function for the thread executor
        def _upload_task(asset):
            success, result = transport.upload_file(asset['full_local_path'], asset['hash'])
            return success, result, asset

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
            # Submit all uploads
            future_to_asset = {
                executor.submit(_upload_task, asset): asset 
                for asset in files_to_upload
            }
            
            for future in concurrent.futures.as_completed(future_to_asset):
                success, result, asset = future.result()
                filename = os.path.basename(asset['full_local_path'])
                
                if success:
                    # Result is the FID (File ID)
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