import json
import os
import time
from pathlib import Path

# Location: ~/.copal/projects.json
REGISTRY_FILE = Path.home() / ".copal" / "projects.json"

def load_registry():
    """Returns a list of known projects sorted by last_accessed."""
    if not REGISTRY_FILE.exists():
        return []
    try:
        with open(REGISTRY_FILE, "r") as f:
            data = json.load(f)
            # Sort by timestamp descending (newest first)
            return sorted(data, key=lambda x: x.get("last_accessed", 0), reverse=True)
    except Exception:
        return []

def register_project(name, local_path, version=None):
    """
    Saves a project to the global list.
    Call this whenever a Push or Pull succeeds.
    """
    projects = load_registry()
    
    # Remove existing entry if it exists (to update it)
    projects = [p for p in projects if p["path"] != str(local_path)]
    
    # Add new entry
    new_entry = {
        "name": name,
        "path": str(local_path),
        "last_version": version,
        "last_accessed": time.time()
    }
    
    projects.insert(0, new_entry) # Add to top
    
    # Save (Keep max 20 recent projects)
    try:
        REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(REGISTRY_FILE, "w") as f:
            json.dump(projects[:20], f, indent=4)
    except Exception as e:
        print(f"⚠️ Failed to update registry: {e}")

def remove_project(local_path):
    """Removes a project from the recent list (e.g., if deleted)."""
    projects = load_registry()
    projects = [p for p in projects if p["path"] != str(local_path)]
    with open(REGISTRY_FILE, "w") as f:
        json.dump(projects, f, indent=4)