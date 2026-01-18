import json
import os
from pathlib import Path

# 1. Define where the config lives (e.g., C:\Users\Name\.copal\config.json)
CONFIG_DIR = Path.home() / ".copal"
CONFIG_FILE = CONFIG_DIR / "config.json"

# 2. Default Settings (Fallback)
DEFAULT_CONFIG = {
    "server_ip": "192.168.178.161",
    "api_port": 8005,
    "filer_port": 8888,
    "default_author": os.getenv("USERNAME", "artist"),
    # If you usually keep projects in one place, set this in the json later!
    "default_projects_root": "" 
}


def load_config():
    """Loads config from JSON, creates it if missing."""
    if not CONFIG_FILE.exists():
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
            print(f"ℹ️  Created new config file at: {CONFIG_FILE}")
        except Exception as e:
            print(f"⚠️  Could not create config file: {e}")
            return DEFAULT_CONFIG
    
    try:
        with open(CONFIG_FILE, "r") as f:
            # Merge user config over defaults (safe update)
            user_config = json.load(f)
            return {**DEFAULT_CONFIG, **user_config}
    except Exception as e:
        print(f"⚠️  Error reading config: {e}. Using defaults.")
        return DEFAULT_CONFIG

# 3. Load immediately
SETTINGS = load_config()

# 4. Export Constants
SERVER_IP = SETTINGS["server_ip"]
API_BASE = f"http://{SERVER_IP}:{SETTINGS['api_port']}"
FILER_BASE = f"http://{SERVER_IP}:{SETTINGS['filer_port']}"

# 5. Centralized Endpoints
ENDPOINTS = {
    "handshake": f"{API_BASE}/handshake",
    "confirm": f"{API_BASE}/confirm_upload",
    "commit": f"{API_BASE}/commit",
    "checkout": f"{API_BASE}/checkout"
}