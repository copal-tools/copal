import os
import requests
import json
import argparse
import hashlib
import sys

# --- CONFIGURATION ---
SERVER_IP = "192.168.178.161"
API_BASE = f"http://{SERVER_IP}:8005"            # Metadata
SEAWEED_FILER_URL = f"http://{SERVER_IP}:8888"  # Filer

def calculate_hash(filepath):
    """Calculates SHA256 hash of a local file to verify integrity."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except FileNotFoundError:
        return None

def download_file(fid, local_path, expected_size, expected_hash):
    """
    Downloads from SeaweedFS Filer.
    The 'fid' is now a path like '/blobs/abc12345...'
    """
    # Construct the full URL
    # We append the 'fid' (which is actually a path) to the base URL
    url = f"{SEAWEED_FILER_URL}{fid}"

    print(f"⬇️  Downloading {os.path.basename(local_path)} ({(expected_size/1024/1024):.2f} MB)... ", end="")
    sys.stdout.flush()

    try:
        # TIMEOUT=None is critical for large files!
        # stream=True ensures we don't load 3GB into RAM
        with requests.get(url, stream=True, timeout=None) as r:

            if r.status_code == 404:
                print("❌ File not found on server!")
                return False

            r.raise_for_status()
            
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): 
                    f.write(chunk)
    
    except Exception as e:
        print(f"❌ Network Error: {e}")
        return False

    # 2. Verify Size
    actual_size = os.path.getsize(local_path)
    if actual_size != expected_size:
        print(f"❌ Size Mismatch! Expected {expected_size}, got {actual_size}")
        return False

    # 3. Verify Hash (Integrity Check)
    local_hash = calculate_hash(local_path)
    if local_hash != expected_hash:
        print(f"❌ Hash Mismatch! Download corrupted.")
        return False

    print("✅ Verified")
    return True

def perform_checkout(project_name, version_tag, target_dir):
    print(f"🌍 Connecting to Asset Hub for {project_name} : {version_tag}...")

    # 1. GET THE MANIFEST
    try:
        url = f"{API_BASE}/checkout/{project_name}/{version_tag}"
        resp = requests.get(url)
        
        if resp.status_code == 404:
            print("❌ Project or Version not found.")
            return
        
        resp.raise_for_status()
        manifest = resp.json()
        
    except Exception as e:
        print(f"❌ Connection to API Failed: {e}")
        return

    files = manifest.get("files", [])
    print(f"📜 Manifest received. Syncing {len(files)} files to: {target_dir}\n")

    # 2. SYNC LOOP
    success_count = 0
    
    for asset in files:
        # [FIX] MAPPING KEYS TO MATCH YOUR API (main.py)
        rel_path = asset["path"]
        fid = asset["fid"]         # Was 'seaweed_fid', now 'fid'
        expected_hash = asset["hash"] # Was 'file_hash', now 'hash'
        expected_size = asset["size"] # Was 'size_bytes', now 'size'

        # Setup local path
        local_dest = os.path.join(target_dir, rel_path)
        os.makedirs(os.path.dirname(local_dest), exist_ok=True)

        # Check if we already have the valid file (Smart Skip)
        if os.path.exists(local_dest):
            if os.path.getsize(local_dest) == expected_size:
                # Only check hash if size matches (optimization)
                if calculate_hash(local_dest) == expected_hash:
                    print(f"⏩ Skipping {rel_path} (Already up to date)")
                    success_count += 1
                    continue

        # Perform Download
        if download_file(fid, local_dest, expected_size, expected_hash):
            success_count += 1
        else:
            print(f"⚠️ Warning: Failed to sync {rel_path}")

    print("-" * 40)
    print(f"✨ Checkout Complete. {success_count}/{len(files)} files ready.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Asset Hub Checkout Client")
    
    parser.add_argument("--project", "-p", required=True, help="Project Name")
    parser.add_argument("--tag", "-t", required=True, help="Version Tag to Checkout")
    parser.add_argument("--dir", "-d", default="checkout_workspace", help="Target Directory")

    args = parser.parse_args()

    # Create target dir if it doesn't exist
    target_path = os.path.abspath(args.dir)
    if not os.path.exists(target_path):
        os.makedirs(target_path)

    perform_checkout(args.project, args.tag, target_path)