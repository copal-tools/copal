"""CopalVX integration for pm-tui: version fetching and subprocess push/pull."""
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def _config() -> dict:
    cfg_path = Path.home() / ".copal" / "config.json"
    if not cfg_path.exists():
        return {}
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _base_url() -> str:
    cfg = _config()
    ip   = cfg.get("server_ip", "192.168.178.161")
    port = cfg.get("api_port", 8005)
    return f"http://{ip}:{port}"


def _client_path() -> str | None:
    return _config().get("client_path")


def get_versions(project_name: str) -> list[str]:
    """Returns version list (newest first), or [] on any error."""
    try:
        url = f"{_base_url()}/projects/{project_name}/versions"
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return []


def health() -> dict:
    """Returns health dict, or {"healthy": False} on error."""
    try:
        url = f"{_base_url()}/health"
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return {"healthy": False, "services": {}}


def run_push(project: str, tag: str, path: str, message: str = "", author: str = "") -> subprocess.Popen:
    """Starts a non-interactive push subprocess. Returns the Popen object."""
    client_dir = _client_path()
    if not client_dir:
        raise RuntimeError("CopalVX client_path not set in ~/.copal/config.json")

    cmd = ["uv", "run", "copalvx", "push", project, tag, path]
    if message:
        cmd += ["--message", message]
    if author:
        cmd += ["--author", author]

    return subprocess.Popen(
        cmd,
        cwd=client_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def run_pull(project: str, tag: str, target: str, policy: str = "backup") -> subprocess.Popen:
    """Starts a non-interactive pull subprocess. Returns the Popen object."""
    client_dir = _client_path()
    if not client_dir:
        raise RuntimeError("CopalVX client_path not set in ~/.copal/config.json")

    cmd = ["uv", "run", "copalvx", "pull", project, tag, target, "--policy", policy]

    return subprocess.Popen(
        cmd,
        cwd=client_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
