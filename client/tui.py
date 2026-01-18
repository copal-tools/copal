import os
import sys
import getpass
import time
from copal_core import fs, api, transport
from copal_core.config import SETTINGS # <--- NEW IMPORT
from copal_core.sync import SyncEngine

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

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

    # --- 3. VERSION TAG ---
    # (In the future we will fetch the 'next' version here automatically)
    tag = input("Version Tag (e.g. v1.0): ").strip()
    if not tag:
        print("❌ Error: Version Tag cannot be empty.")
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
    except Exception as e:
        print(f"❌ Commit Failed: {e}")

    input("\nPress Enter to return to menu...")

def do_pull():
    print_header()
    print(">>> PULL (RESTORE) MODE")
    
    # 1. Inputs
    project = input("Project Name: ").strip()
    tag = input("Version Tag: ").strip()
    
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
        choice = input("Select Option: ").strip()
        
        if choice == "1":
            do_push()
        elif choice == "2":
            do_pull()
        elif choice == "3":
            print("Bye!")
            sys.exit()

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\nExiting...")