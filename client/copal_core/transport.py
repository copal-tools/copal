import os
import sys
import requests
from .config import FILER_BASE

def upload_file(file_path, file_hash):
    """Uploads a file to SeaweedFS Filer (force SSD)."""
    file_size = os.path.getsize(file_path)
    target_url = f"{FILER_BASE}/blobs/{file_hash}?disk=ssd"

    # --- DEBUG PRINTS ---
    print(f"\n[DEBUG] File: {file_path}")
    print(f"[DEBUG] Hash: {file_hash}")
    print(f"[DEBUG] Size: {file_size}")
    print(f"[DEBUG] Target URL: {target_url}")
    # --------------------

    print(f"⬆️  Uploading {os.path.basename(file_path)} ({(file_size/1024/1024):.2f} MB)... ", end="")
    sys.stdout.flush()

    try:
        with open(file_path, 'rb') as f:
            headers = {'Expect': '100-continue', 'Content-Length': str(file_size)}
            
            # --- DEBUG: Print Headers ---
            print(f"\n[DEBUG] Headers: {headers}")
            
            response = requests.put(target_url, data=f, headers=headers, timeout=None)
            
            # --- DEBUG: Print Response ---
            print(f"[DEBUG] Status Code: {response.status_code}")
            print(f"[DEBUG] Response Text: {response.text}")
            
            if response.status_code in [200, 201]:
                print("✅ Success")
                return True, f"/blobs/{file_hash}"
            else:
                print(f"❌ Failed ({response.status_code})")
                return False, None
    except Exception as e:
        print(f"❌ Error: {e}")
        return False, None

def download_file(fid, local_path, expected_size):
    """Downloads a file from SeaweedFS Filer."""
    url = f"{FILER_BASE}{fid}"
    print(f"\n[DEBUG] Requesting URL: {url}")  # <--- Add this!

    print(f"⬇️  Downloading {os.path.basename(local_path)} ({(expected_size/1024/1024):.2f} MB)... ", end="")
    sys.stdout.flush()

    try:
        with requests.get(url, stream=True, timeout=None) as r:
            if r.status_code == 404:
                print("❌ Not Found")
                return False
            r.raise_for_status()
            
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): 
                    f.write(chunk)
        print("✅")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False