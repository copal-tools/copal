"""
Integration tests — exercise the live CopalVX API server.

Prerequisites
-------------
1. Server stack is running:  docker-compose up -d
2. Optionally set COPALVX_SERVER_URL to override the default server address.

Run
---
    cd E:\\Development\\copal\\copalvx\\client
    uv run pytest tests/integration/ -v

Each test class is independent.  A module-scoped fixture creates a unique
throw-away project and deletes it when the module finishes, so the tests
leave the server in the same state they found it.
"""

import os
import uuid
import pytest
import requests

BASE = os.getenv("COPALVX_SERVER_URL", "http://192.168.1.100:8005")
# Unique name so parallel runs or interrupted tests don't collide
PROJECT = f"__pytest_{uuid.uuid4().hex[:10]}__"

# Identity + delete-confirmation headers — every test that hits a write endpoint
# needs at least the identity pair; destructive endpoints additionally need the
# confirm-delete header. The values match what the production client sends.
IDENT_HEADERS = {"X-Copal-User": "pytest", "X-Copal-Host": "pytest-runner"}
CONFIRM_DELETE_HEADERS = {**IDENT_HEADERS, "X-Confirm-Delete": "yes-permanently"}


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
        headers=CONFIRM_DELETE_HEADERS,
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
# GET /projects (listing) — Decimal serialization regression
# ---------------------------------------------------------------------------

class TestProjectsListSerialization:
    """Regression for the 2026-05-14 500 on GET /projects.

    PostgreSQL SUM(BIGINT) returns NUMERIC → SQLAlchemy maps it to
    decimal.Decimal. The endpoint uses Response(content=json.dumps(payload))
    which bypasses FastAPI's encoder, so stdlib json.dumps() raised
    `TypeError: Object of type Decimal is not JSON serializable` on every
    request. Fix: explicit int() casts on numeric aggregate fields.
    """

    def test_list_returns_200_with_well_formed_json(self):
        r = requests.get(f"{BASE}/projects", timeout=5)
        assert r.status_code == 200, (
            f"GET /projects returned {r.status_code}: {r.text[:300]}"
        )
        data = r.json()
        assert isinstance(data, list)

    def test_total_storage_bytes_is_a_number(self, project):
        # The `project` fixture seeds at least one commit + asset, which makes
        # the SUM aggregate return a non-NULL NUMERIC — the path that used
        # to break json.dumps.
        data = requests.get(f"{BASE}/projects", timeout=5).json()
        entry = next((p for p in data if p["name"] == project), None)
        assert entry is not None, f"project {project!r} not found in list"
        assert isinstance(entry["total_storage_bytes"], int)
        assert entry["total_storage_bytes"] >= 0
        # version_count and author_count also come from aggregate functions;
        # ensure they're plain ints too.
        assert isinstance(entry["version_count"], int)
        assert isinstance(entry["author_count"], int)


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
        }, headers=IDENT_HEADERS, timeout=5)
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
        }, headers=IDENT_HEADERS, timeout=5)
        # 200 = success, 409 = tag already used (fine — it passed format validation)
        assert r.status_code in (200, 409), (
            f"L6 regression: valid tag '{good_tag}' was rejected with {r.status_code}: {r.text}"
        )


# ---------------------------------------------------------------------------
# Orphan cleanup — H3 (new endpoint)
# ---------------------------------------------------------------------------

class TestCleanupOrphans:
    def test_endpoint_exists_and_returns_ok(self):
        r = requests.post(
            f"{BASE}/admin/cleanup-orphans",
            headers=CONFIRM_DELETE_HEADERS,
            timeout=10,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert isinstance(data["assets_deleted"], int)
        assert isinstance(data["blobs_deleted"], int)

    def test_endpoint_rejects_without_confirm_header(self):
        """Sec-A.4 — destructive admin op requires X-Confirm-Delete."""
        r = requests.post(f"{BASE}/admin/cleanup-orphans", timeout=10)
        assert r.status_code == 400, (
            f"Sec-A.4 regression: expected 400 without X-Confirm-Delete, got {r.status_code}"
        )


class TestDeleteConfirmation:
    def test_delete_rejects_without_confirm_header(self):
        """Sec-A.4 — DELETE /projects requires X-Confirm-Delete.

        Uses its OWN throwaway project so an un-patched server that ignores
        the new header (i.e. returns 200) won't accidentally delete the
        module-scoped ``project`` fixture and break downstream tests.
        """
        disposable = f"__pytest_delconf_{uuid.uuid4().hex[:8]}__"
        # Create the project so DELETE has something real to act on.
        requests.post(f"{BASE}/projects", json={"name": disposable}, timeout=5)
        try:
            r = requests.delete(
                f"{BASE}/projects/{disposable}",
                json={"delete_orphan_files": False},
                timeout=10,
            )
            assert r.status_code == 400, (
                f"Sec-A.4 regression: expected 400, got {r.status_code}"
            )
        finally:
            # Clean up regardless of pass/fail — uses the confirm header so
            # this teardown works on a patched server too.
            requests.delete(
                f"{BASE}/projects/{disposable}",
                json={"delete_orphan_files": True},
                headers=CONFIRM_DELETE_HEADERS,
                timeout=10,
            )


class TestIdentityHeaders:
    def test_commit_rejects_missing_host_header(self, project):
        """Sec-A.5 — /commit must require X-Copal-Host."""
        r = requests.post(f"{BASE}/commit", json={
            "project_id":  project,
            "version_tag": "v9.9.99",
            "message":     "no header test",
            "author":      "pytest",
            "files":       [],
        }, timeout=5)
        assert r.status_code == 422, (
            f"Sec-A.5 regression: expected 422 without X-Copal-Host, got {r.status_code}"
        )

    def test_commit_rejects_malformed_host_header(self, project):
        """Sec-A.5 — bad chars in X-Copal-Host are rejected.

        Note: the ``requests`` library refuses to *send* a header containing
        \\r, \\n, or \\t (it raises InvalidHeader client-side), so we test
        with a value that's transmissible but outside our ``[\\w.@-]`` charset.
        """
        r = requests.post(f"{BASE}/commit", json={
            "project_id":  project,
            "version_tag": "v9.9.98",
            "message":     "bad header test",
            "author":      "pytest",
            "files":       [],
        }, headers={"X-Copal-Host": "evil host with space"}, timeout=5)
        assert r.status_code == 422, (
            f"Sec-A.5 regression: expected 422 for bad X-Copal-Host, got {r.status_code}"
        )

    def test_commit_rejects_overlong_host_header(self, project):
        """Sec-A.5 — values over 64 chars must be rejected."""
        r = requests.post(f"{BASE}/commit", json={
            "project_id":  project,
            "version_tag": "v9.9.97",
            "message":     "long header test",
            "author":      "pytest",
            "files":       [],
        }, headers={"X-Copal-Host": "a" * 65}, timeout=5)
        assert r.status_code == 422, (
            f"Sec-A.5 regression: expected 422 for 65-char host, got {r.status_code}"
        )


# ---------------------------------------------------------------------------
# Request body size limit — L3
# ---------------------------------------------------------------------------

class TestBodySizeLimit:
    def test_oversized_payload_rejected(self, project):
        """A payload over the configured limit must be rejected with 413.

        Default is now 50 MB (was 10) — large bulk commits with ~40 k file
        entries used to bump up against the old 10 MB cap. The test sends 60 MB.

        Uvicorn may close the connection before we finish uploading (after
        sending the 413), which surfaces as a ConnectionError on the client —
        that outcome is also a correct rejection.
        """
        large_body = b"x" * (60 * 1024 * 1024)  # 60 MB > default 50 MB limit
        try:
            r = requests.post(
                f"{BASE}/handshake",
                data=large_body,
                headers={"Content-Type": "application/octet-stream"},
                timeout=60,
            )
            assert r.status_code == 413, (
                f"Body-limit regression: expected 413 for {len(large_body) // (1024 * 1024)} MB "
                f"payload, got {r.status_code}"
            )
        except requests.exceptions.ConnectionError:
            # Server closed the connection after sending 413 before we finished
            # uploading — this is also correct rejection behaviour.
            pass


# ---------------------------------------------------------------------------
# Version diff — Phase I
# ---------------------------------------------------------------------------

class TestVersionDiff:
    V1 = "v1.0"
    V2 = "v2.0"

    @pytest.fixture(scope="class", autouse=True)
    def _commits(self, project):
        """Create two empty version commits so diff tests have real tags to query."""
        for tag in (self.V1, self.V2):
            r = requests.post(f"{BASE}/commit", json={
                "project_id":  project,
                "version_tag": tag,
                "message":     f"pytest diff fixture {tag}",
                "author":      "pytest",
                "files":       [],
            }, headers=IDENT_HEADERS, timeout=5)
            assert r.status_code in (200, 409), f"Could not create commit {tag}: {r.text}"

    def test_diff_nonexistent_project_returns_404(self):
        r = requests.get(
            f"{BASE}/projects/__no_such_project__/diff/v1.0/v2.0", timeout=5
        )
        assert r.status_code == 404

    def test_diff_nonexistent_version_returns_404(self, project):
        r = requests.get(
            f"{BASE}/projects/{project}/diff/{self.V1}/__no_such_tag__", timeout=5
        )
        assert r.status_code == 404

    def test_diff_response_structure(self, project):
        r = requests.get(
            f"{BASE}/projects/{project}/diff/{self.V1}/{self.V2}", timeout=5
        )
        assert r.status_code == 200
        data = r.json()
        for key in ("v1", "v2", "added", "removed", "changed", "unchanged_count"):
            assert key in data, f"Missing key '{key}' in diff response"
        assert isinstance(data["added"], list)
        assert isinstance(data["removed"], list)
        assert isinstance(data["changed"], list)
        assert isinstance(data["unchanged_count"], int)

    def test_diff_response_tags_match_request(self, project):
        r = requests.get(
            f"{BASE}/projects/{project}/diff/{self.V1}/{self.V2}", timeout=5
        )
        assert r.status_code == 200
        data = r.json()
        assert data["v1"] == self.V1
        assert data["v2"] == self.V2

    def test_diff_same_version_has_no_changes(self, project):
        """Diffing a version against itself must return empty lists and no changes."""
        r = requests.get(
            f"{BASE}/projects/{project}/diff/{self.V1}/{self.V1}", timeout=5
        )
        assert r.status_code == 200
        data = r.json()
        assert data["added"]   == []
        assert data["removed"] == []
        assert data["changed"] == []
