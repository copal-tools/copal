import os
import sys
import getpass
from copal_core import fs, api, transport

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_header():
    clear_screen()
    print("========================================")
    print("   COPAL-VX  |  Asset Management TUI    ")
    print("========================================")
    print("")

def do_push():
    print_header()
    print(">>> PUSH (UPLOAD) MODE")
    
    # 1. Inputs
    project = input("Project Name: ").strip()
    tag = input("Version Tag (e.g. v1.0): ").strip()
    msg = input("Commit Message: ").strip()
    
    # --- FIX: Ask for directory explicitly ---
    # We default to the current folder, but allow pasting a path like "E:\Projects\MyMovie"
    cwd = os.getcwd()
    path_input = input(f"Source Directory [Default: {cwd}]: ").strip()
    
    if not path_input:
        root_dir = cwd
    else:
        # Clean up path (remove quotes if user dragged-and-dropped folder into terminal)
        root_dir = path_input.replace('"', '').replace("'", "")
    
    author = getpass.getuser()
    
    if not project or not tag:
        print("❌ Error: Project and Tag are required.")
        input("Press Enter...")
        return

    # Check if folder exists
    if not os.path.exists(root_dir):
        print(f"❌ Error: Directory not found: {root_dir}")
        input("Press Enter...")
        return

    # 2. Logic
    print(f"\n📂 Scanning: {root_dir}")
    local_assets = fs.scan_directory(root_dir)
    
    if not local_assets:
        print("⚠️ No files found.")
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

    # 3. Upload Loop
    if needed:
        print(f"\n📦 Uploading {len(needed)} new files...")
        to_upload = [f for f in local_assets if f["path"] in needed]
        
        for asset in to_upload:
            success, fid = transport.upload_file(asset['full_local_path'], asset['hash'])
            if success:
                api.confirm_upload(asset['hash'], asset['size'], fid)
            else:
                print("❌ Aborting due to upload failure.")
                input("Press Enter...")
                return
    else:
        print("\n⚡ All files exist on server. Skipping uploads.")

    # 4. Commit
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
    target_dir = input(f"Target Directory [Default: ./Restored_{tag}]: ").strip()
    
    if not target_dir:
        target_dir = f"Restored_{tag}"
        
    if not project or not tag:
        return

    # 2. Manifest
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
    print(f"📜 Found {len(files)} files.")
    
    # 3. Download Loop
    abs_target = os.path.abspath(target_dir)
    if not os.path.exists(abs_target):
        os.makedirs(abs_target)
        
    success_count = 0
    for asset in files:
        rel_path = asset["path"]
        fid = asset["fid"]
        
        local_dest = os.path.join(abs_target, rel_path)
        os.makedirs(os.path.dirname(local_dest), exist_ok=True)
        
        # Smart Skip
        if os.path.exists(local_dest):
            if os.path.getsize(local_dest) == asset["size"]:
                if fs.calculate_hash(local_dest) == asset["hash"]:
                    print(f"⏩ {rel_path} (Skipped)")
                    success_count += 1
                    continue
        
        if transport.download_file(fid, local_dest, asset["size"]):
            success_count += 1
            
    print(f"\n✨ Restore Complete. {success_count}/{len(files)} files ready.")
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