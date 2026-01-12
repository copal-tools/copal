import os
import hashlib

def calculate_hash(filepath):
    """Calculates SHA256 hash of a local file."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except FileNotFoundError:
        return None

def scan_directory(root_dir):
    """Recursively scans a directory and returns a list of file metadata."""
    file_list = []
    print(f"🔍 Scanning directory: {root_dir}")
    
    ignore_files = {".DS_Store", "Thumbs.db", ".git", "__pycache__", ".venv"}

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignore_files]
        
        for file in files:
            if file in ignore_files:
                continue
                
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, root_dir).replace("\\", "/")
            
            try:
                file_size = os.path.getsize(full_path)
                file_hash = calculate_hash(full_path)
                
                file_list.append({
                    "path": rel_path,
                    "hash": file_hash,
                    "size": file_size,
                    "full_local_path": full_path 
                })
            except OSError:
                print(f"⚠️ Skipping inaccessible file: {full_path}")
            
    return file_list