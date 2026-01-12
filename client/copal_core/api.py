import requests
from .config import ENDPOINTS

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
    resp = requests.post(ENDPOINTS["handshake"], json=payload)
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
    requests.post(ENDPOINTS["confirm"], json=payload)

def commit(project, tag, message, author, files):
    """Finalizes the version."""
    payload = {
        "project_id": project,
        "version_tag": tag,
        "message": message,
        "author": author,
        "files": [{"path": f["path"], "hash": f["hash"], "size": f["size"]} for f in files]
    }
    requests.post(ENDPOINTS["commit"], json=payload).raise_for_status()

def get_manifest(project, tag):
    """Fetches file list for a specific version."""
    url = f"{ENDPOINTS['checkout']}/{project}/{tag}"
    resp = requests.get(url)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()