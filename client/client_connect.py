import os
import hashlib
import requests
import json
import argparse
import getpass  # To get the current computer username automatically

# --- CONFIGURATION ---
# (Ideally, this should eventually move to a config file, but constant here is fine for now)
SERVER_IP = "192.168.178.161"
API_BASE = f"http://{SERVER_IP}:8005"
FILER_BASE = f"http://{SERVER_IP}:8888"  # <--- NEW: Direct Filer Access

HANDSHAKE_URL = f"{API_BASE}/handshake"
CONFIRM_URL = f"{API_BASE}/confirm_upload"
COMMIT_URL = f"{API_BASE}/commit"


def calculate_hash(filepath):
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()

def upload_file(file_path, upload_url):
    """
    Uploads a file to SeaweedFS Filer.
    The Filer handles chunking for large files automatically.
    """
    file_size = os.path.getsize(file_path)
    print(f"Uploading {os.path.basename(file_path)} ({(file_size / (1024*1024)):.2f} MB)...", end="")

    try:
        with open(file_path, 'rb') as f:
            # HEADERS ARE CRITICAL:
            # 1. 'Expect': '100-continue' asks server permission before sending 2GB data.
            # 2. 'Content-Length' is good practice for binary streams.
            headers = {
                'Expect': '100-continue',
                'Content-Length': str(file_size)
            }
            
            # TIMEOUT IS CRITICAL:
            # Set to None (Infinite) so Python doesn't hang up after 60s.
            # We use .put() for raw binary stream (better than .post for huge files).
            response = requests.put(
                upload_url, 
                data=f, 
                headers=headers,
                timeout=None # Infinite timeout for large files
            )
            
            if response.status_code in [200, 201]:
                print(" -> Success")
                return True
            else:
                print(f" -> Failed: {response.status_code} {response.text}")
                return False

    except Exception as e:
        print(f" -> Failed: {e}")
        return False


def scan_directory(root_dir):
    file_list = []
    print(f"🔍 Scanning directory: {root_dir}")
    
    # Files to ignore
    ignore_files = {".DS_Store", "Thumbs.db", ".git", "__pycache__", ".venv"}

    for root, dirs, files in os.walk(root_dir):
        # Filter out ignored directories
        dirs[:] = [d for d in dirs if d not in ignore_files]
        
        for file in files:
            if file in ignore_files:
                continue
                
            full_path = os.path.join(root, file)
            # Make path relative (e.g., "assets/textures/wood.png")
            rel_path = os.path.relpath(full_path, root_dir).replace("\\", "/")
            
            file_size = os.path.getsize(full_path)
            file_hash = calculate_hash(full_path)
            
            file_list.append({
                "path": rel_path,
                "hash": file_hash,
                "size": file_size,
                "full_local_path": full_path 
            })
            
    return file_list


def perform_sync(project_name, version_tag, commit_message, author, root_dir):
    # 1. SCAN
    local_assets = scan_directory(root_dir)
    print(f"Found {len(local_assets)} files.")
    
    if not local_assets:
        print("⚠️ No files found to sync.")
        return

    # 2. HANDSHAKE
    print("Handshaking with server...")
    payload = {
        "client_id": "laptop-01",
        "project_id": project_name,
        "client_manifest": [
            {"path": f["path"], "hash": f["hash"], "size": f["size"]} 
            for f in local_assets
        ]
    }
    
    try:
        resp = requests.post(HANDSHAKE_URL, json=payload)
        resp.raise_for_status()
        server_response = resp.json()
    except Exception as e:
        print(f"❌ Handshake failed: {e}")
        # Add this to see the server's specific complaint:
        if hasattr(e, 'response') and e.response is not None:
             print(f"Server Detail: {e.response.text}")
        return

    # --- ADD THESE 2 LINES HERE ---
    print("---------------- DEBUG RESPONSE ----------------")
    print(json.dumps(server_response, indent=2))
    print("------------------------------------------------")

    needed_files = set(server_response.get("required_files", []))
    
    # 3. UPLOAD LOOP
    if not needed_files:
        print("✅ Files are already on server. Proceeding to commit...")
    else:
        print(f"Need to upload: {len(needed_files)} files.")
        
        # Filter local assets to find the ones we need to upload
        files_to_upload = [f for f in local_assets if f["path"] in needed_files]
        
        
        for file_info in files_to_upload:
            f_hash = file_info["hash"]

            target_url = f"{FILER_BASE}/blobs/{f_hash}?disk=ssd"
            success = upload_file(file_info['full_local_path'], target_url)

            if success:
                # Confirm success to the Database
                try:
                    confirm_payload = {
                        "file_hash": f_hash,
                        "size_bytes": file_info['size'],
                        # We save the PATH as the ID now
                        "seaweed_fid": f"/blobs/{f_hash}", 
                        "mime_type": "application/octet-stream"
                    }
                    requests.post(CONFIRM_URL, json=confirm_payload)
                except Exception as e:
                    print(f" -> DB Confirmation Failed: {e}")
            else:
                print(f"❌ Upload failed for {file_info['path']}, aborting commit.")
                return

    # 4. SEND COMMIT SNAPSHOT
    print("Finalizing Commit...")
    commit_payload = {
        "project_id": project_name,
        "message": commit_message,
        "author": author,
        "version_tag": version_tag,
        "files": [
            {"path": f["path"], "hash": f["hash"], "size": f["size"]} 
            for f in local_assets
        ]
    }
    
    try:
        resp = requests.post(COMMIT_URL, json=commit_payload)
        resp.raise_for_status()
        print(f"✅ COMMIT SUCCESSFUL! Saved as {version_tag}")
    except Exception as e:
        print(f"❌ Commit failed: {e}")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Asset Sync Client")
    
    # Required Arguments
    parser.add_argument("--project", "-p", required=True, help="Name of the project")
    parser.add_argument("--tag", "-t", required=True, help="Version tag (e.g. v1.0)")
    
    # Optional Arguments
    parser.add_argument("--dir", "-d", default=".", help="Directory to sync (default: current dir)")
    parser.add_argument("--message", "-m", default="Auto-sync update", help="Commit message")
    parser.add_argument("--author", "-a", default=getpass.getuser(), help="Author name (default: OS user)")

    args = parser.parse_args()

    # Run the sync
    perform_sync(
        project_name=args.project,
        version_tag=args.tag,
        commit_message=args.message,
        author=args.author,
        root_dir=args.dir
    )