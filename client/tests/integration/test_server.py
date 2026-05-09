"""
Integration tests — exercise the live CopalVX API server.

Prerequisites
-------------
1. Server stack is running:  docker-compose up -d
2. Optionally set COPALVX_SERVER_URL to override the default server address.

Run
---
    cd E:\\Development\\Copal-VX\\client
    uv run pytest tests/integration/ -v

Each test class is independent.  A module-scoped fixture creates a unique
throw-away project and deletes it when the module finishes, so the tests
leave the server in the same state they found it.
"""

import os
import uuid
import pytest
import requests

BASE = os.getenv("COPALVX_SERVER_URL", "http://192.168.178.161:8005")
# Unique name so parallel runs or interrupted tests don't collide
PROJECT = f"__pytest_{uuid.uuid4().hex[:10]}__"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _ensure_server():
    """Skip the entire module if the server is not reachable."""
    try:
        requests.get(f"{BASE}/health", timeout=4)
    except Exception:
        pytest.skip(f"Server not reachable at {BASE} — skipping integration tests.")


@pytest.fixture(scope="module")
def project():
    """Create the test project once, yield its name, delete it after all tests."""
    r = requests.post(f"{BASE}/projects", json={"name": PROJECT}, timeout=5)
    assert r.status_code in (201, 409), f"Failed to create test project: {r.text}"
    yield PROJECT
    requests.delete(
        f"{BASE}/projects/{PROJECT}",
        json={"delete_orphan_files": True},
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_200_with_service_map(self):
        r = requests.get(f"{BASE}/health", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert "healthy" in data
        assert set(data["services"].keys()) >= {"api", "database", "seaweedfs"}


# ---------------------------------------------------------------------------
# Project metadata — H2 fix (LEFT JOIN, no 404 on zero-commit project)
# ---------------------------------------------------------------------------

class TestMetadataNoCommits:
    def test_project_with_no_commits_returns_200(self, project):
        r = requests.get(f"{BASE}/projects/{project}/metadata", timeout=5)
        assert r.status_code == 200, (
            "H2 regression: project with no commits returned "
            f"{r.status_code} instead of 200"
        )

    def test_no_commit_metadata_has_null_version(self, project):
        data = requests.get(f"{BASE}/projects/{project}/metadata", timeout=5).json()
        assert data["latest_version"] is None
        assert data["total_size_bytes"] == 0
        assert data["authors"] == []

    def test_nonexistent_project_returns_404(self):
        r = requests.get(f"{BASE}/projects/__does_not_exist_xyz__/metadata", timeout=5)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Handshake with multiple files — C2 fix (IN → ANY)
# ---------------------------------------------------------------------------

class TestHandshake:
    def test_single_file_handshake(self, project):
        r = requests.post(f"{BASE}/handshake", json={
            "project_id": project,
            "client_manifest": [
                {"path": "a.txt", "hash": "a" * 64, "size": 10},
            ],
        }, timeout=5)
        assert r.status_code == 200
        assert "a.txt" in r.json()["required_files"]

    def test_multi_file_handshake_does_not_crash(self, project):
        """C2 fix: multi-file IN :hashes query used to crash with psycopg2 tuple binding."""
        manifest = [
            {"path": f"file_{i}.bin", "hash": chr(ord("a") + i) * 64, "size": i + 1}
            for i in range(5)
        ]
        r = requests.post(f"{BASE}/handshake", json={
            "project_id": project,
            "client_manifest": manifest,
        }, timeout=5)
        assert r.status_code == 200, f"C2 regression: {r.status_code} {r.text}"
        # All hashes are unknown to the server — all should be required
        assert len(r.json()["required_files"]) == 5

    def test_empty_manifest_returns_empty_required(self, project):
        r = requests.post(f"{BASE}/handshake", json={
            "project_id": project,
            "client_manifest": [],
        }, timeout=5)
        assert r.status_code == 200
        assert r.json()["required_files"] == []


# ---------------------------------------------------------------------------
# Bulk confirm — M3 fix (N+1 → single round-trip)
# ---------------------------------------------------------------------------

class TestBulkConfirm:
    def test_empty_list_succeeds(self):
        r = requests.post(f"{BASE}/confirm_uploads", json={"files": []}, timeout=5)
        assert r.status_code == 200
        assert r.json()["recorded"] == 0

    def test_blob_missing_from_seaweedfs_returns_422_or_503(self):
        """Server must reject a hash whose blob isn't in SeaweedFS."""
        r = requests.post(f"{BASE}/confirm_uploads", json={
            "files": [{
                "file_hash":   "f" * 64,
                "size_bytes":  999,
                "seaweed_fid": "/blobs/definitelynotreal",
                "mime_type":   "application/octet-stream",
            }]
        }, timeout=5)
        # 422 = blob not found; 503 = SeaweedFS unreachable (both are valid rejections)
        assert r.status_code in (422, 503), (
            f"M3 regression: expected 422/503, got {r.status_code}"
        )


# ---------------------------------------------------------------------------
# Version tag validation — L6 fix (server-side regex guard)
# ---------------------------------------------------------------------------

class TestVersionTagValidation:
    @pytest.mark.parametrize("bad_tag", [
        "v1/0",          # slash breaks URL routing
        "v1 0",          # space
        "v1.0!",         # special char
        "",              # empty
        "my release",    # spaces and no version structure
    ])
    def test_invalid_tags_return_422(self, project, bad_tag):
        r = requests.post(f"{BASE}/commit", json={
            "project_id":  project,
            "version_tag": bad_tag,
            "message":     "test",
            "author":      "pytest",
            "files":       [],
        }, timeout=5)
        assert r.status_code == 422, (
            f"L6 regression: tag '{bad_tag}' should be rejected but got {r.status_code}"
        )

    @pytest.mark.parametrize("good_tag", [
        "v1.0", "v1.2.3", "v2.0-rc1", "1.0", "v10",
    ])
    def test_valid_tags_pass_validation(self, project, good_tag):
        """Valid tags must not be rejected by the format check.

        An empty commit (no files) on a real project should succeed with 200.
        """
        r = requests.post(f"{BASE}/commit", json={
            "project_id":  project,
            "version_tag": good_tag,
            "message":     "pytest validation check",
            "author":      "pytest",
            "files":       [],
        }, timeout=5)
        # 200 = success, 409 = tag already used (fine — it passed format validation)
        assert r.status_code in (200, 409), (
            f"L6 regression: valid tag '{good_tag}' was rejected with {r.status_code}: {r.text}"
        )


# ---------------------------------------------------------------------------
# Orphan cleanup — H3 (new endpoint)
# ---------------------------------------------------------------------------

class TestCleanupOrphans:
    def test_endpoint_exists_and_returns_ok(self):
        r = requests.post(f"{BASE}/admin/cleanup-orphans", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert isinstance(data["assets_deleted"], int)
        assert isinstance(data["blobs_deleted"], int)


# ---------------------------------------------------------------------------
# Request body size limit — L3
# ---------------------------------------------------------------------------

class TestBodySizeLimit:
    def test_oversized_payload_rejected(self, project):
        """L3 fix: a payload genuinely over 10 MB must be rejected with 413.

        The requests library always sets Content-Length to the actual body size,
        so we have to send real data.  11 MB on a LAN transfers in < 1 s and the
        middleware rejects based on Content-Length before reading the body.

        Uvicorn may close the connection before we finish uploading (after
        sending the 413), which surfaces as a ConnectionError on the client —
        that outcome is also a correct rejection.
        """
        large_body = b"x" * (11 * 1024 * 1024)  # 11 MB > default 10 MB limit
        try:
            r = requests.post(
                f"{BASE}/handshake",
                data=large_body,
                headers={"Content-Type": "application/octet-stream"},
                timeout=30,
            )
            assert r.status_code == 413, (
                f"L3 regression: expected 413 for {len(large_body) // (1024 * 1024)} MB "
                f"payload, got {r.status_code}"
            )
        except requests.exceptions.ConnectionError:
            # Server closed the connection after sending 413 before we finished
            # uploading — this is also correct rejection behaviour.
            pass
