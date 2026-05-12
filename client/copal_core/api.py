import requests
from requests.exceptions import ConnectionError, Timeout
from .config import ENDPOINTS, API_BASE

API_TIMEOUT = (10, 30)

def handshake(project_name, local_assets):
    """Asks server which files are missing."""
    payload = {
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
    """Tells DB that a single upload finished successfully. Kept for compatibility."""
    payload = {
        "file_hash": file_hash,
        "size_bytes": size,
        "seaweed_fid": fid,
        "mime_type": "application/octet-stream"
    }
    resp = requests.post(ENDPOINTS["confirm"], json=payload, timeout=API_TIMEOUT)
    resp.raise_for_status()


def confirm_uploads(items):
    """Bulk confirm — records all uploaded blobs in a single request.

    :param items: list of dicts with keys 'hash', 'size', 'fid'
    :raises: requests.HTTPError on server error, ConnectionError if unreachable
    """
    payload = {
        "files": [
            {
                "file_hash": item["hash"],
                "size_bytes": item["size"],
                "seaweed_fid": item["fid"],
                "mime_type": "application/octet-stream",
            }
            for item in items
        ]
    }
    resp = requests.post(f"{API_BASE}/confirm_uploads", json=payload, timeout=API_TIMEOUT)
    resp.raise_for_status()
    return resp.json()

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


def get_health():
    """Returns health dict {healthy, services}. Raises ConnectionError if unreachable."""
    try:
        resp = requests.get(f"{API_BASE}/health", timeout=(5, 10))
        resp.raise_for_status()
        return resp.json()
    except (ConnectionError, Timeout) as e:
        raise ConnectionError(f"Cannot reach server at {API_BASE}") from e


def list_projects():
    """Returns list of project dicts from GET /projects."""
    try:
        resp = requests.get(f"{API_BASE}/projects", timeout=API_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (ConnectionError, Timeout) as e:
        raise ConnectionError(f"Cannot reach server at {API_BASE}") from e


def get_metadata(project_name):
    """Returns metadata dict. Raises ValueError on 404, ConnectionError if unreachable."""
    try:
        resp = requests.get(f"{API_BASE}/projects/{project_name}/metadata", timeout=API_TIMEOUT)
        if resp.status_code == 404:
            raise ValueError(resp.json().get("detail", "Not found"))
        resp.raise_for_status()
        return resp.json()
    except (ConnectionError, Timeout) as e:
        raise ConnectionError(f"Cannot reach server at {API_BASE}") from e


def rename_project(old_name, new_name):
    """Renames a project. Raises ValueError on 404/409, ConnectionError if unreachable."""
    try:
        resp = requests.patch(
            f"{API_BASE}/projects/{old_name}",
            json={"new_name": new_name},
            timeout=API_TIMEOUT,
        )
        if resp.status_code == 404:
            raise ValueError(f"Project '{old_name}' not found.")
        if resp.status_code == 409:
            raise ValueError(f"Project '{new_name}' already exists.")
        resp.raise_for_status()
        return resp.json()
    except (ConnectionError, Timeout) as e:
        raise ConnectionError(f"Cannot reach server at {API_BASE}") from e


def get_server_stats():
    """Returns server-wide stats dict. Raises ConnectionError if unreachable."""
    try:
        resp = requests.get(f"{API_BASE}/server/stats", timeout=API_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (ConnectionError, Timeout) as e:
        raise ConnectionError(f"Cannot reach server at {API_BASE}") from e


def update_description(project_name, description):
    """Sets a project's description. Raises ValueError on 404."""
    try:
        resp = requests.patch(
            f"{API_BASE}/projects/{project_name}/description",
            json={"description": description},
            timeout=API_TIMEOUT,
        )
        if resp.status_code == 404:
            raise ValueError(f"Project '{project_name}' not found.")
        resp.raise_for_status()
        return resp.json()
    except (ConnectionError, Timeout) as e:
        raise ConnectionError(f"Cannot reach server at {API_BASE}") from e


def get_diff(project, v1, v2):
    """Returns diff dict {v1, v2, added, removed, changed, unchanged_count}. None on 404."""
    try:
        resp = requests.get(
            f"{API_BASE}/projects/{project}/diff/{v1}/{v2}",
            timeout=API_TIMEOUT,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except (ConnectionError, Timeout) as e:
        raise ConnectionError(f"Cannot reach server at {API_BASE}") from e


def delete_project(project_name, delete_orphan_files=False):
    """Deletes a project. Returns response dict. Raises ValueError on 404."""
    try:
        resp = requests.delete(
            f"{API_BASE}/projects/{project_name}",
            json={"delete_orphan_files": delete_orphan_files},
            timeout=(10, 60),
        )
        if resp.status_code == 404:
            raise ValueError(f"Project '{project_name}' not found.")
        resp.raise_for_status()
        return resp.json()
    except (ConnectionError, Timeout) as e:
        raise ConnectionError(f"Cannot reach server at {API_BASE}") from e
