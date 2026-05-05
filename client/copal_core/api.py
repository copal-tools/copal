import requests
from requests.exceptions import ConnectionError, Timeout
from .config import ENDPOINTS, API_BASE

API_TIMEOUT = (10, 30)

def handshake(project_name, local_assets):
    """Asks server which files are missing."""
    payload = {
        "client_id": "tui-client",
        "project_id": project_name,
        "client_manifest": [
            {"path": f["path"], "hash": f["hash"], "size": f["size"]}
            for f in local_assets
        ]
    }
    resp = requests.post(ENDPOINTS["handshake"], json=payload, timeout=API_TIMEOUT)
    resp.raise_for_status()
    return resp.json()

def confirm_upload(file_hash, size, fid):
    """Tells DB that an upload finished successfully."""
    payload = {
        "file_hash": file_hash,
        "size_bytes": size,
        "seaweed_fid": fid,
        "mime_type": "application/octet-stream"
    }
    resp = requests.post(ENDPOINTS["confirm"], json=payload, timeout=API_TIMEOUT)
    resp.raise_for_status()

def commit(project, tag, message, author, files):
    """Finalizes the version."""
    payload = {
        "project_id": project,
        "version_tag": tag,
        "message": message,
        "author": author,
        "files": [{"path": f["path"], "hash": f["hash"], "size": f["size"]} for f in files]
    }
    resp = requests.post(ENDPOINTS["commit"], json=payload, timeout=API_TIMEOUT)
    resp.raise_for_status()

def get_manifest(project, tag):
    """Fetches file list for a specific version."""
    url = f"{ENDPOINTS['checkout']}/{project}/{tag}"
    resp = requests.get(url, timeout=API_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()

def ensure_project(name, description=""):
    """Creates the project if it doesn't exist. 409 = already exists = success."""
    payload = {"name": name, "description": description}
    resp = requests.post(f"{API_BASE}/projects", json=payload, timeout=API_TIMEOUT)
    if resp.status_code in (201, 409):
        return
    resp.raise_for_status()

def get_versions(project_name):
    """Fetches list of versions from server (Newest First).
    Returns empty list for genuine 404. Raises on connection/server errors.
    """
    url = f"{API_BASE}/projects/{project_name}/versions"
    try:
        resp = requests.get(url, timeout=API_TIMEOUT)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return resp.json()
    except (ConnectionError, Timeout) as e:
        raise ConnectionError(f"Cannot reach server at {API_BASE}. Is it running?") from e
