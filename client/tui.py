import os
import sys
import getpass
import time
import subprocess
from copal_core import fs, api, transport, versioning, registry
from copal_core.config import SETTINGS
from copal_core.sync import SyncEngine
# pm_hooks wires CopalVX push/pull events into the ProjectRegistry pm system.
# All hooks are non-fatal — if pm tools are absent, CopalVX continues normally.
from copal_core import pm_hooks

def clear_screen():
    subprocess.run(['cls' if os.name == 'nt' else 'clear'], shell=True, capture_output=True)

def print_header():
    clear_screen()
    print("========================================")
    print("   COPAL-VX  |  Asset Management TUI    ")
    print("========================================")
    print("")

def print_progress(current, total, message):
    """Simple text-based progress bar."""
    percent = (current / total) * 100
    bar_length = 30
    filled_length = int(bar_length * current // total)
    bar = '█' * filled_length + '-' * (bar_length - filled_length)
    
    # \r returns to start of line, allowing overwrite
    sys.stdout.write(f"\r[{bar}] {percent:.1f}% | {message[:40]:<40}")
    sys.stdout.flush()

def do_push():
    print_header()
    print(">>> PUSH (UPLOAD) MODE")
    
    # --- 1. ASK FOR DIRECTORY FIRST ---
    cwd = os.getcwd()
    default_path = cwd
    
    # Load default root from config if exists
    if SETTINGS.get("default_projects_root") and os.path.exists(SETTINGS["default_projects_root"]):
        default_path = SETTINGS["default_projects_root"]

    path_input = input(f"Source Directory [Default: {default_path}]: ").strip()
    
    if not path_input:
        root_dir = default_path
    else:
        # Sanitize Windows paths
        root_dir = path_input.replace('"', '').replace("'", "")
    
    if not os.path.exists(root_dir):
        print(f"❌ Error: Directory does not exist: {root_dir}")
        input("Press Enter...")
        return
        
    # --- 2. AUTO-DETECT PROJECT NAME ---
    # Get the folder name (e.g., "MyMovie") from the path
    folder_name = os.path.basename(os.path.normpath(root_dir))
    
    project = input(f"Project Name [Default: {folder_name}]: ").strip()
    if not project:
        project = folder_name # Use the default if user hit Enter

    # --- SMART VERSIONING ---
    print("☁️  Checking remote versions...")
    try:
        existing_tags = api.get_versions(project)
    except Exception as e:
        print(f"❌ {e}")
        input("Press Enter...")
        return

    default_tag = "v1.0"
    if existing_tags:
        latest = existing_tags[0] 
        default_tag = versioning.increment_tag(latest)
        print(f"ℹ️  Latest on server: {latest}")
    else:
        print("ℹ️  New Project (No remote versions found)")

    while True:
        tag_input = input(f"Version Tag [Default: {default_tag}]: ").strip()
        
        if not tag_input:
            tag = default_tag
        else:
            tag = versioning.ensure_prefix(tag_input)
            
        is_valid, err_msg = versioning.validate_push_tag(tag, existing_tags)
        if is_valid:
            break
        print(f"❌ Error: {err_msg}")
        
    print(f"✅ Selected: {tag}")

    # --- 3.5. VERIFY/CREATE PROJECT ---
    print("🔍 Verifying project on server...")
    try:
        api.ensure_project(project)
    except Exception as e:
        print(f"❌ Cannot confirm project: {e}")
        input("Press Enter...")
        return

    # --- 4. COMMIT MESSAGE ---
    default_msg = f"Update {tag}"
    msg = input(f"Commit Message [Default: {default_msg}]: ").strip()
    if not msg:
        msg = default_msg
        
    author = SETTINGS.get("default_author", getpass.getuser())

    # --- 5. EXECUTE ---
    print(f"\n📂 Scanning: {root_dir}")
    # Hook 1 (pre-push): flush any pending time sessions into project.yaml so
    # time data is included in this push and available on other machines.
    pm_hooks.hook_pre_push(root_dir)
    local_assets = fs.scan_directory(root_dir)
    
    if not local_assets:
        print("⚠️ No files found (or all files were ignored).")
        input("Press Enter...")
        return

    print("\n🤝 Handshaking...")
    try:
        resp = api.handshake(project, local_assets)
        needed = set(resp.get("required_files", []))
    except Exception as e:
        print(f"❌ Server Error: {e}")
        input("Press Enter...")
        return

    # Upload Loop (Same as before)
    if needed:
        print(f"\n📦 Uploading {len(needed)} new files...")
        to_upload = [f for f in local_assets if f["path"] in needed]
        
        # Initialize Engine
        engine = SyncEngine(max_threads=8)
        
        print("🚀 Starting Parallel Upload...")
        
        # Run Uploads
        successful_uploads = engine.execute_upload_plan(to_upload, progress_callback=print_progress)
        
        print(f"\n✨ Uploads Finished. Success: {len(successful_uploads)}/{len(to_upload)}")
        
        if len(successful_uploads) != len(to_upload):
            print("⚠️  Some files failed to upload. Aborting commit to protect integrity.")
            input("Press Enter...")
            return

        # Confirm to DB (We can do this sequentially as it's just metadata, very fast)
        print("📝 Confirming uploads to database...")
        try:
            for item in successful_uploads:
                api.confirm_upload(item['hash'], item['size'], item['fid'])
        except Exception as e:
            print(f"❌ DB Error: {e}")
            return
    else:
        print("\n⚡ All files exist on server. Skipping uploads.")

    # Commit
    print("\n📝 Committing...")
    try:
        api.commit(project, tag, msg, author, local_assets)
        print(f"\n✅ SUCCESS! Project '{project}' version '{tag}' saved.")
        fs.save_local_state(root_dir, project, tag)
        registry.register_project(project, root_dir, tag)
        # Hook 2 (post-push): stamp the copalvx block in project.yaml with the
        # project name and version tag that was just successfully committed.
        pm_hooks.hook_post_push(root_dir, project, tag)
        print("💾 Added to Recent Projects list.")
    except Exception as e:
        print(f"❌ Commit Failed: {e}")

    input("\nPress Enter to return to menu...")

def do_pull():
    print_header()
    print(">>> PULL (RESTORE) MODE")
    
    # 1. Inputs
    project = input("Project Name: ").strip()
    if not project: return

    # --- SMART SELECTION MENU ---
    print("☁️  Fetching history...")
    try:
        versions = api.get_versions(project)
    except Exception as e:
        print(f"❌ {e}")
        input("Press Enter...")
        return

    if not versions:
        print("❌ No versions found for this project.")
        input("Press Enter...")
        return

    print("\n--- Available Versions ---")
    for i, v in enumerate(versions[:10]):
        label = " (Latest)" if i == 0 else ""
        print(f"   {i+1}. {v}{label}")
    print("--------------------------")
    
    tag = ""
    while not tag:
        choice = input(f"Select Version [1-{len(versions)}] or type 'latest': ").strip().lower()
        
        if choice in ["", "latest", "l"]:
            tag = versions[0]
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(versions):
                tag = versions[idx]
            else:
                print("❌ Invalid number.")
        else:
            tag = versioning.ensure_prefix(choice)
            if tag not in versions:
                 print(f"⚠️  Warning: '{tag}' not found in history list. Trying anyway...")

    print(f"✅ Selected: {tag}")
    
    # Default to current folder + Project Name
    cwd = os.getcwd()
    default_target = os.path.join(cwd, project) if project else cwd
    
    target_dir_input = input(f"Target Directory [Default: {default_target}]: ").strip()
    target_dir = target_dir_input if target_dir_input else default_target
    
    # --- NEW: Conflict Policy ---
    print("\n[?] How should we handle existing files that differ from the server?")
    print("    1. Backup (Rename local to .bak) [Default]")
    print("    2. Overwrite (Destroy local changes)")
    print("    3. Skip (Keep local changes)")
    
    policy_map = {"1": "backup", "2": "overwrite", "3": "skip"}
    choice = input("Select Policy [1-3]: ").strip()
    policy = policy_map.get(choice, "backup")
    
    if not project or not tag:
        return

    # 2. Fetch Manifest
    print("\n🌍 Fetching Manifest...")
    try:
        manifest = api.get_manifest(project, tag)
        if not manifest:
            print("❌ Project or Version not found.")
            input("Press Enter...")
            return
    except Exception as e:
        print(f"❌ Error: {e}")
        input("Press Enter...")
        return

    files = manifest.get("files", [])
    print(f"📜 Manifest received: {len(files)} files.")
    
    # 3. Generate Plan
    print("🧠 Analyzing filesystem (Smart Sync)...")
    
    # Initialize Engine with 8 threads (SSD optimized)
    engine = SyncEngine(conflict_policy=policy, max_threads=8)
    
    # Run the math (Move detection, diff checks)
    plan = engine.generate_plan(files, target_dir)
    
    # 4. Show Summary
    counts = {"DOWNLOAD": 0, "LOCAL_COPY": 0, "SKIP": 0, "BACKUP": 0, "OVERWRITE": 0}
    
    for task in plan:
        act = task["action"]
        conflict = task.get("conflict_mode") # Check if there is a conflict action hidden here
        
        # Count Conflicts First
        if conflict == "BACKUP" or act == "BACKUP":
            counts["BACKUP"] += 1
        elif conflict == "OVERWRITE" or act == "OVERWRITE":
            counts["OVERWRITE"] += 1
            
        # Count Transport Method
        if act == "LOCAL_COPY":
            counts["LOCAL_COPY"] += 1
        elif act == "DOWNLOAD":
            counts["DOWNLOAD"] += 1
        elif "SKIP" in act:
            counts["SKIP"] += 1

    print("\n--- Sync Plan ---")
    print(f"⬇️  Download:    {counts['DOWNLOAD']}")
    print(f"📦 Local Copy:  {counts['LOCAL_COPY']} (Saved bandwidth!)")
    print(f"🛡️  Backup:      {counts['BACKUP']}")
    print(f"⏩ Skip:        {counts['SKIP']}")
    print("-----------------")
    
    if input("Proceed? (Y/n): ").lower() == 'n':
        print("Aborted.")
        input("Press Enter...")
        return

    # 5. Execute in Parallel
    print("\n🚀 Starting Sync...")
    start_time = time.time()
    
    # Pass the callback for the progress bar
    results = engine.execute_plan(plan, progress_callback=print_progress)
    
    duration = time.time() - start_time
    print(f"\n\n✨ Complete in {duration:.2f}s.")
    print(f"✅ Success: {results['success']} | ❌ Fail: {results['fail']} | ⏩ Skipped: {results['skip']}")
    
    # Save state so we remember this project
    fs.save_local_state(target_dir, project, tag)
    registry.register_project(project, target_dir, tag)
    # Hooks 3 & 4 (post-pull): register the project in the pm registry so
    # `pm list` and `project` CWD detection work, then display the CopalVX
    # block from the pulled project.yaml to confirm project identity.
    pm_hooks.hook_post_pull(target_dir, project, tag)

    input("\nPress Enter to return to menu...")

def main_menu():
    while True:
        print_header()
        print(f"Server: {transport.FILER_BASE}")
        print("----------------------------------------")
        print("1. Push (Upload current folder)")
        print("2. Pull (Restore version)")
        print("3. Exit")
        print("----------------------------------------")
        choice = input("Select Option: ").strip().lower()

        if choice in ("1", "push", "p"):
            do_push()
        elif choice in ("2", "pull"):
            do_pull()
        elif choice in ("3", "exit", "q", "quit"):
            print("Bye!")
            sys.exit()
        else:
            print(f"❌ '{choice}' is not a valid option. Type 1, 2, or 3.")
            input("Press Enter to continue...")

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\nExiting...")