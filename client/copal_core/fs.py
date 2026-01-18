import os
import hashlib
import fnmatch
import json

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

def load_ignore_rules(root_dir):
    """
    Loads ignore patterns from a .copalignore file in the root_dir.
    Always includes default system junk.
    """
    # Base defaults
    rules = {".DS_Store", "Thumbs.db", ".git", "__pycache__", ".venv", ".copal"}
    
    ignore_path = os.path.join(root_dir, ".copalignore")
    if os.path.exists(ignore_path):
        try:
            with open(ignore_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        rules.add(line)
            print("ℹ️  Loaded .copalignore rules.")
        except Exception as e:
            print(f"⚠️  Error reading .copalignore: {e}")
            
    return rules

def should_ignore(path, root_dir, rules):
    """Checks if a file matches any ignore rule (supports wildcards like *.tmp)."""
    rel_path = os.path.relpath(path, root_dir)
    filename = os.path.basename(path)
    
    for rule in rules:
        # Check standard filename match (e.g., "Thumbs.db")
        if rule == filename:
            return True
        # Check wildcard match (e.g., "*.jpg")
        if fnmatch.fnmatch(filename, rule):
            return True
        # Check folder match (e.g., "renders/")
        if rule.endswith("/") and rule.strip("/") in rel_path.split(os.sep):
            return True
            
    return False

def scan_directory(root_dir):
    """Recursively scans a directory with .copalignore support."""
    file_list = []
    print(f"🔍 Scanning directory: {root_dir}")
    
    # Load rules once
    ignore_rules = load_ignore_rules(root_dir)

    for root, dirs, files in os.walk(root_dir):
        # 1. Filter Directories (in-place modification needed for os.walk)
        # We iterate backwards to safely remove items
        for i in range(len(dirs) - 1, -1, -1):
            full_dir_path = os.path.join(root, dirs[i])
            if should_ignore(full_dir_path, root_dir, ignore_rules):
                del dirs[i]
        
        # 2. Filter Files
        for file in files:
            full_path = os.path.join(root, file)
            
            if should_ignore(full_path, root_dir, ignore_rules):
                continue
                
            # Make path relative (e.g., "assets/textures/wood.png")
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

def load_local_state(root_dir):
    """
    Reads .copal/state.json to find previous project info.
    Returns dict or None.
    """
    state_file = os.path.join(root_dir, ".copal", "state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_local_state(root_dir, project_id, last_tag):
    """
    Saves project info to .copal/state.json so we remember it next time.
    """
    copal_dir = os.path.join(root_dir, ".copal")
    os.makedirs(copal_dir, exist_ok=True)
    
    state_file = os.path.join(copal_dir, "state.json")
    data = {
        "project_id": project_id,
        "last_tag": last_tag,
        "last_updated": os.path.getmtime(root_dir)
    }
    
    try:
        with open(state_file, "w") as f:
            json.dump(data, f, indent=4)
        # Hide the folder on Windows
        if os.name == 'nt':
            os.system(f'attrib +h "{copal_dir}"')
    except Exception as e:
        print(f"⚠️  Could not save local state: {e}")