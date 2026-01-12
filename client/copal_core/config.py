import os

# Default to your specific server, but allow Environment Variable override
SERVER_IP = os.getenv("COPAL_SERVER_IP", "192.168.178.161")

API_PORT = "8005"
FILER_PORT = "8888"

# Derived URLs
API_BASE = f"http://{SERVER_IP}:{API_PORT}"
FILER_BASE = f"http://{SERVER_IP}:{FILER_PORT}"

# Endpoints
ENDPOINTS = {
    "handshake": f"{API_BASE}/handshake",
    "confirm": f"{API_BASE}/confirm_upload",
    "commit": f"{API_BASE}/commit",
    "checkout": f"{API_BASE}/checkout"
}