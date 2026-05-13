import hashlib
import json
import logging
import os
import re
import requests
from urllib.parse import urlparse
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from database import get_db

# Logging — level controlled via LOG_LEVEL env var (default INFO).
# In production set LOG_LEVEL=WARNING to silence debug noise.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# SeaweedFS Internal (API -> Master / Filer)
SEAWEED_MASTER_URL = os.getenv("SEAWEED_MASTER_URL", "http://127.0.0.1:9333")
_master = urlparse(SEAWEED_MASTER_URL)
SEAWEED_FILER_PORT = int(os.getenv("SEAWEED_FILER_PORT", "8888"))
SEAWEED_FILER_URL = f"{_master.scheme}://{_master.hostname}:{SEAWEED_FILER_PORT}"

# Public IP (What we send to the client)
# In Docker this comes from the compose env. Locally, defaults to your server IP.
SERVER_PUBLIC_IP = os.getenv("PUBLIC_ACCESS_HOST", "192.168.1.100")


def get_upload_url(replication="000"):
    """Asks SeaweedFS for a file ID, then rewrites the URL to use the public IP."""
    try:
        response = requests.get(f"{SEAWEED_MASTER_URL}/dir/assign?replication={replication}")
        data = response.json()

        internal_url_str = data.get('publicUrl') or data.get('url')
        logger.debug("SeaweedFS assigned internal URL: %s", internal_url_str)

        port = internal_url_str.split(":")[-1]
        corrected_url = f"http://{SERVER_PUBLIC_IP}:{port}/{data['fid']}"
        logger.debug("Rewritten upload URL for client: %s", corrected_url)

        return {
            "fid": data["fid"],
            "upload_url": corrected_url
        }
    except Exception as e:
        logger.error("Failed to get upload URL from SeaweedFS: %s", e)
        return None


app = FastAPI()

# ── Request body size guard ────────────────────────────────────────────────────
# Rejects manifests or payloads over the configured limit before they reach any
# endpoint. File uploads go directly to SeaweedFS and never pass through here,
# so this only affects JSON API calls (handshake manifests, commit payloads).
#
# Default raised to 50 MB (was 10) so bulk commits with ~40 k file entries
# don't get rejected; per-entry overhead is ~250 bytes so 50 MB ≈ 200 k files.
# Tune via env: MAX_REQUEST_BODY_MB=N.
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_MB", "50")) * 1024 * 1024


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    """Reject oversized payloads based on the Content-Length header.

    A streaming-body counter that wrapped ``request._receive`` to catch
    forged Content-Length headers was attempted earlier, but Starlette's
    ``BaseHTTPMiddleware`` doesn't reliably propagate the override into
    ``call_next``'s downstream request — and a stray ``return`` in the
    ``finally`` block ended up swallowing real endpoint exceptions. For a
    LAN-only system the Content-Length check is sufficient; revisit if the
    deployment ever fronts an untrusted network.
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                limit_mb = MAX_REQUEST_BODY_BYTES // (1024 * 1024)
                return Response(
                    content=f"Request body exceeds {limit_mb} MB limit.",
                    status_code=413,
                )
        except ValueError:
            pass
    return await call_next(request)


# ── Identity / delete-confirmation header helpers ──────────────────────────────
# Identity headers (X-Copal-User / X-Copal-Host) feed straight into the events
# audit log. We bound the length and restrict the character set so a misbehaving
# client can't pump nonsense into the log or smuggle ANSI escape codes.
_IDENT_RE = re.compile(r"^[\w.@-]{1,64}$")

CONFIRM_DELETE_HEADER = "X-Confirm-Delete"
CONFIRM_DELETE_VALUE = "yes-permanently"


def _require_ident(header_value: Optional[str], header_name: str) -> str:
    if not header_value or not _IDENT_RE.match(header_value):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Header {header_name!r} must be 1-64 chars of letters, digits, "
                "underscore, hyphen, period or '@'."
            ),
        )
    return header_value


def _require_confirm_delete(header_value: Optional[str]) -> None:
    if header_value != CONFIRM_DELETE_VALUE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Destructive operation requires header "
                f"'{CONFIRM_DELETE_HEADER}: {CONFIRM_DELETE_VALUE}'."
            ),
        )

# --- DATA MODELS ---

class AssetEntry(BaseModel):
    path: str
    hash: str
    size: int

class HandshakeRequest(BaseModel):
    project_id: str
    client_manifest: List[AssetEntry]

class HandshakeResponse(BaseModel):
    required_files: List[str]
    message: str

class ConfirmUploadRequest(BaseModel):
    file_hash: str
    size_bytes: int
    seaweed_fid: str
    mime_type: str = "application/octet-stream"

class CommitRequest(BaseModel):
    project_id: str
    message: str
    author: str
    version_tag: str
    files: List[AssetEntry]

class UploadRequest(BaseModel):
    files: List[str]

class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""

class DeleteProjectRequest(BaseModel):
    delete_orphan_files: bool = False

class RenameProjectRequest(BaseModel):
    new_name: str

class BulkConfirmRequest(BaseModel):
    files: List[ConfirmUploadRequest]

class UpdateDescriptionRequest(BaseModel):
    description: str


# --- ENDPOINTS ---

@app.post("/projects", status_code=201)
def create_project(request: CreateProjectRequest, db: Session = Depends(get_db)):
    logger.info("Creating project: %s", request.name)
    try:
        result = db.execute(
            text("INSERT INTO projects (name, description) VALUES (:name, :desc) RETURNING id"),
            {"name": request.name, "desc": request.description}
        )
        project_id = result.fetchone()[0]
        db.commit()
        return {"project_id": str(project_id), "name": request.name, "status": "created"}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Project '{request.name}' already exists.")


@app.post("/handshake", response_model=HandshakeResponse)
def handshake(request: HandshakeRequest, db: Session = Depends(get_db)):
    logger.info("Handshake for project: %s (%d files)", request.project_id, len(request.client_manifest))

    client_hashes = {asset.hash for asset in request.client_manifest}

    if not client_hashes:
        return {"required_files": [], "message": "Manifest was empty."}

    query = text("SELECT file_hash FROM assets WHERE file_hash = ANY(:hashes)")
    result = db.execute(query, {"hashes": list(client_hashes)})
    known_hashes = {row[0] for row in result}

    files_to_upload = []
    skipped_count = 0

    for asset in request.client_manifest:
        if asset.hash in known_hashes:
            skipped_count += 1
        else:
            files_to_upload.append(asset.path)

    msg = f"Checked {len(request.client_manifest)} files. Database found {skipped_count}. Need {len(files_to_upload)} new files."
    logger.info(msg)

    return {"required_files": files_to_upload, "message": msg}


@app.post("/get_upload_urls")
def get_urls(request: UploadRequest):
    logger.info("Generating upload URLs for %d files", len(request.files))

    upload_map = {}
    for file_path in request.files:
        assignment = get_upload_url()
        if assignment:
            upload_map[file_path] = assignment

    return {"upload_map": upload_map}


@app.post("/confirm_upload")
def confirm_upload(request: ConfirmUploadRequest, db: Session = Depends(get_db)):
    logger.info("Confirming upload: %s... -> %s", request.file_hash[:8], request.seaweed_fid)

    # Verify the blob actually landed in SeaweedFS before recording it.
    try:
        head = requests.head(f"{SEAWEED_FILER_URL}{request.seaweed_fid}", timeout=5)
        if head.status_code != 200:
            logger.warning("Blob not found in SeaweedFS: %s (HTTP %s)", request.seaweed_fid, head.status_code)
            raise HTTPException(status_code=422, detail="Upload not found in storage. Re-upload the file.")
    except HTTPException:
        raise
    except requests.RequestException as e:
        logger.error("Could not reach SeaweedFS filer to verify blob: %s", e)
        raise HTTPException(status_code=503, detail="Storage unavailable. Try again.")

    query = text("""
        INSERT INTO assets (file_hash, size_bytes, seaweed_fid, mime_type)
        VALUES (:hash, :size, :fid, :mime)
        ON CONFLICT (file_hash) DO NOTHING
    """)

    try:
        db.execute(query, {
            "hash": request.file_hash,
            "size": request.size_bytes,
            "fid": request.seaweed_fid,
            "mime": request.mime_type
        })
        db.commit()
        return {"status": "ok", "message": "Asset recorded."}
    except Exception as e:
        db.rollback()
        logger.error("DB error in confirm_upload (hash=%s): %s", request.file_hash[:8], e)
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.post("/confirm_uploads")
def confirm_uploads_bulk(request: BulkConfirmRequest, db: Session = Depends(get_db)):
    """Bulk version of /confirm_upload — records all uploaded blobs in one round-trip.

    All blobs are verified against SeaweedFS before any DB write. If any
    verification fails the entire request is rejected so the asset store stays
    consistent. The client should re-upload the missing files and try again.
    """
    if not request.files:
        return {"status": "ok", "recorded": 0}

    logger.info("Bulk confirm: verifying %d blob(s) in SeaweedFS.", len(request.files))

    # Verify every blob exists before touching the DB
    missing = []
    for item in request.files:
        try:
            head = requests.head(f"{SEAWEED_FILER_URL}{item.seaweed_fid}", timeout=5)
            if head.status_code != 200:
                missing.append(item.file_hash[:8])
        except requests.RequestException as e:
            logger.error("Could not reach SeaweedFS filer to verify %s: %s", item.seaweed_fid, e)
            raise HTTPException(status_code=503, detail="Storage unavailable. Try again.")

    if missing:
        logger.warning("Bulk confirm rejected — %d blob(s) missing from SeaweedFS: %s", len(missing), missing)
        raise HTTPException(
            status_code=422,
            detail=f"{len(missing)} blob(s) not found in storage: {missing}. Re-upload and try again.",
        )

    # All blobs confirmed — write everything in a single transaction
    try:
        db.execute(
            text("""
                INSERT INTO assets (file_hash, size_bytes, seaweed_fid, mime_type)
                VALUES (:hash, :size, :fid, :mime)
                ON CONFLICT (file_hash) DO NOTHING
            """),
            [
                {"hash": f.file_hash, "size": f.size_bytes, "fid": f.seaweed_fid, "mime": f.mime_type}
                for f in request.files
            ],
        )
        db.commit()
        logger.info("Bulk confirm: recorded %d asset(s).", len(request.files))
        return {"status": "ok", "recorded": len(request.files)}
    except Exception as e:
        db.rollback()
        logger.error("DB error in confirm_uploads: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error.")


@app.post("/commit")
def create_commit(
    request: CommitRequest,
    x_copal_host: str = Header(..., alias="X-Copal-Host"),
    db: Session = Depends(get_db),
):
    x_copal_host = _require_ident(x_copal_host, "X-Copal-Host")
    safe_author = _require_ident(request.author, "author")
    logger.info("Creating commit '%s' for project '%s'", request.version_tag, request.project_id)

    # Validate tag format — a slash would silently break /checkout/{name}/{tag}
    # URL routing; spaces and other special chars cause subtle retrieval bugs.
    # Valid examples: v1.0  v1.2.3  v2.0-rc1  1.0
    if not re.match(r"^v?\d+(\.\d+)*(-\w+)?$", request.version_tag):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid version_tag '{request.version_tag}'. "
                "Must be digits and dots with an optional 'v' prefix and optional "
                "'-word' suffix — e.g. 'v1.0', 'v1.2.3', 'v2.0-rc1'."
            ),
        )

    # --- STEP 1: Resolve project ---
    project = db.execute(
        text("SELECT id FROM projects WHERE name = :name"),
        {"name": request.project_id}
    ).fetchone()

    if not project:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{request.project_id}' not found. Create it first with a push."
        )
    project_id = project[0]

    # --- STEP 2: Validate ALL hashes resolve BEFORE writing anything ---
    # If any upload silently failed, we catch it here and abort cleanly.
    file_map = {f.path: f.hash for f in request.files}
    unique_hashes = list(set(file_map.values()))

    if unique_hashes:
        asset_rows = db.execute(
            text("SELECT file_hash, id FROM assets WHERE file_hash = ANY(:hashes)"),
            {"hashes": unique_hashes}
        ).fetchall()
        hash_to_uuid = {row[0]: row[1] for row in asset_rows}

        missing = [h[:8] for h in unique_hashes if h not in hash_to_uuid]
        if missing:
            logger.warning(
                "Commit '%s' aborted — %d file(s) not in asset store: %s",
                request.version_tag, len(missing), missing
            )
            raise HTTPException(
                status_code=422,
                detail=f"{len(missing)} file(s) were not found in the asset store. "
                       f"One or more uploads may have failed. Re-run the push to retry."
            )
    else:
        hash_to_uuid = {}

    # --- STEP 3: Write everything in a single atomic transaction ---
    try:
        # Insert commit row
        commit_result = db.execute(text("""
            INSERT INTO commits (project_id, version_tag, message, author_name)
            VALUES (:pid, :tag, :msg, :auth)
            RETURNING id
        """), {
            "pid": project_id,
            "tag": request.version_tag,
            "msg": request.message,
            "auth": safe_author
        })
        commit_id = commit_result.fetchone()[0]

        # Bulk insert file links (all hashes already validated above)
        links_to_create = [
            {"cid": commit_id, "aid": hash_to_uuid[f.hash], "path": f.path}
            for f in request.files
            if f.hash in hash_to_uuid
        ]

        if links_to_create:
            db.execute(text("""
                INSERT INTO project_files (commit_id, asset_id, file_path)
                VALUES (:cid, :aid, :path)
            """), links_to_create)

        # Record the push event for the activity log
        db.execute(text("""
            INSERT INTO events (project_id, kind, version_tag, user_name, client_host)
            VALUES (:pid, 'push', :tag, :user, :host)
        """), {
            "pid":  project_id,
            "tag":  request.version_tag,
            "user": safe_author,
            "host": x_copal_host,
        })

        # Single commit — all or nothing
        db.commit()
        logger.info("Commit '%s' created with %d files.", request.version_tag, len(links_to_create))
        return {"commit_id": str(commit_id), "status": "success"}

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Version '{request.version_tag}' already exists for project '{request.project_id}'."
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error("Commit '%s' failed unexpectedly: %s", request.version_tag, e)
        raise HTTPException(status_code=500, detail="Commit failed. All changes rolled back.")


@app.get("/checkout/{project_name}/{version_tag}")
def checkout_version(
    project_name: str,
    version_tag: str,
    x_copal_user: str = Header(..., alias="X-Copal-User"),
    x_copal_host: str = Header(..., alias="X-Copal-Host"),
    db: Session = Depends(get_db),
):
    x_copal_user = _require_ident(x_copal_user, "X-Copal-User")
    x_copal_host = _require_ident(x_copal_host, "X-Copal-Host")
    logger.info("Checkout: %s @ %s by %s@%s", project_name, version_tag, x_copal_user, x_copal_host)

    query = text("""
        SELECT c.id, p.id
        FROM commits c
        JOIN projects p ON c.project_id = p.id
        WHERE p.name = :pname AND c.version_tag = :vtag
    """)
    result = db.execute(query, {"pname": project_name, "vtag": version_tag}).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Project or Version not found")

    commit_id, project_id = result[0], result[1]

    # Record the pull event for the activity log
    try:
        db.execute(text("""
            INSERT INTO events (project_id, kind, version_tag, user_name, client_host)
            VALUES (:pid, 'pull', :tag, :user, :host)
        """), {
            "pid":  project_id,
            "tag":  version_tag,
            "user": x_copal_user,
            "host": x_copal_host,
        })
        db.commit()
    except Exception as e:
        # Event log failure must not break the pull itself
        db.rollback()
        logger.warning("Failed to record pull event: %s", e)

    files_query = text("""
        SELECT pf.file_path, a.seaweed_fid, a.file_hash, a.size_bytes
        FROM project_files pf
        JOIN assets a ON pf.asset_id = a.id
        WHERE pf.commit_id = :cid
    """)
    files = db.execute(files_query, {"cid": commit_id}).fetchall()

    manifest = [
        {"path": row[0], "fid": row[1], "hash": row[2], "size": row[3]}
        for row in files
    ]

    return {
        "project": project_name,
        "version": version_tag,
        "file_count": len(manifest),
        "files": manifest
    }


@app.get("/projects/{project_name}/versions")
def get_project_versions(project_name: str, db: Session = Depends(get_db)):
    logger.info("Fetching versions for: %s", project_name)

    query = text("""
        SELECT c.version_tag
        FROM commits c
        JOIN projects p ON c.project_id = p.id
        WHERE p.name = :name
        ORDER BY c.created_at DESC
    """)
    rows = db.execute(query, {"name": project_name}).fetchall()
    logger.debug("Found %d versions for '%s'", len(rows), project_name)

    if not rows:
        return []

    return [row[0] for row in rows]


@app.get("/projects/{project_name}/events")
def get_project_events(project_name: str, limit: int = 50, db: Session = Depends(get_db)):
    """Recent push/pull activity for a project, newest first."""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")

    rows = db.execute(text("""
        SELECT e.kind, e.version_tag, e.user_name, e.client_host, e.created_at
        FROM events e
        JOIN projects p ON e.project_id = p.id
        WHERE p.name = :name
        ORDER BY e.created_at DESC
        LIMIT :limit
    """), {"name": project_name, "limit": limit}).fetchall()

    return [
        {
            "kind":        row[0],
            "version_tag": row[1],
            "user":        row[2],
            "host":        row[3],
            "created_at":  row[4].isoformat() if row[4] else None,
        }
        for row in rows
    ]


@app.get("/projects/{project_name}/metadata")
def get_project_metadata(project_name: str, db: Session = Depends(get_db)):
    logger.info("Fetching metadata for: %s", project_name)

    # LEFT JOIN so a project that exists but has no commits yet still returns a row
    # (commit columns will be NULL). INNER JOIN was causing spurious 404s on new projects.
    query_info = text("""
        SELECT p.id, p.created_at, p.description, c.id, c.version_tag, c.author_name, c.created_at, c.message
        FROM projects p
        LEFT JOIN commits c ON p.id = c.project_id
        WHERE p.name = :name
        ORDER BY c.created_at DESC NULLS LAST
        LIMIT 1
    """)
    row = db.execute(query_info, {"name": project_name}).fetchone()

    # No row at all → project genuinely doesn't exist
    if not row:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")

    project_id, p_created, description, commit_id, tag, author, c_created, msg = row

    # commit_id is None when the project exists but has never been pushed to
    total_bytes = 0
    if commit_id is not None:
        query_size = text("""
            SELECT SUM(a.size_bytes)
            FROM project_files pf
            JOIN assets a ON pf.asset_id = a.id
            WHERE pf.commit_id = :cid
        """)
        size_result = db.execute(query_size, {"cid": commit_id}).fetchone()
        total_bytes = size_result[0] if size_result[0] else 0

    authors_rows = db.execute(text("""
        SELECT DISTINCT c.author_name
        FROM commits c WHERE c.project_id = :pid
    """), {"pid": project_id}).fetchall()

    return {
        "project": project_name,
        "description": description or "",
        "latest_version": tag,
        "author": author,
        "authors": [r[0] for r in authors_rows],
        "updated_at": c_created.isoformat() if c_created else None,
        "created_at": p_created.isoformat(),
        "total_size_bytes": total_bytes,
        "total_size_mb": round(total_bytes / (1024 * 1024), 2),
        "message": msg
    }


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    status = {"api": "ok", "database": "ok", "seaweedfs": "ok"}

    try:
        db.execute(text("SELECT 1"))
    except Exception:
        status["database"] = "unreachable"

    try:
        r = requests.get(f"{SEAWEED_MASTER_URL}/cluster/status", timeout=3)
        if r.status_code != 200:
            status["seaweedfs"] = "degraded"
    except Exception:
        status["seaweedfs"] = "unreachable"

    healthy = all(v == "ok" for v in status.values())
    return {"healthy": healthy, "services": status}


@app.get("/projects")
def list_projects(
    request: Request,
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
    db: Session = Depends(get_db),
):
    # Cheap "did anything change?" probe. The dashboard polls this endpoint
    # every 60 s; on a quiet server we want to skip the expensive correlated
    # subqueries below. The ETag is derived from (project count, latest
    # commit timestamp) — both come from indexed columns.
    cache_probe = db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM projects) AS pc,
            (SELECT MAX(created_at) FROM commits) AS mc
    """)).fetchone()
    project_count = cache_probe[0] if cache_probe else 0
    last_commit = cache_probe[1] if cache_probe else None
    etag_seed = f"{project_count}:{last_commit.isoformat() if last_commit else 'none'}"
    etag = '"' + hashlib.sha256(etag_seed.encode()).hexdigest()[:16] + '"'

    if if_none_match and if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})

    rows = db.execute(text("""
        SELECT p.name, p.created_at,
               COUNT(DISTINCT c.id) AS version_count,
               COUNT(DISTINCT c.author_name) AS author_count,
               MAX(c.created_at) AS last_push,
               (SELECT c2.version_tag FROM commits c2
                WHERE c2.project_id = p.id
                ORDER BY c2.created_at DESC LIMIT 1) AS latest_version,
               (SELECT c2.author_name FROM commits c2
                WHERE c2.project_id = p.id
                ORDER BY c2.created_at DESC LIMIT 1) AS last_author,
               (SELECT COALESCE(SUM(a.size_bytes), 0)
                FROM assets a
                WHERE a.id IN (
                    SELECT DISTINCT pf.asset_id FROM project_files pf
                    JOIN commits c3 ON pf.commit_id = c3.id
                    WHERE c3.project_id = p.id
                )) AS total_storage_bytes
        FROM projects p
        LEFT JOIN commits c ON c.project_id = p.id
        GROUP BY p.id
        ORDER BY MAX(c.created_at) DESC NULLS LAST
    """)).fetchall()

    payload = [
        {
            "name": r[0],
            "created_at": r[1].isoformat() if r[1] else None,
            "version_count": r[2],
            "author_count": r[3],
            "last_push": r[4].isoformat() if r[4] else None,
            "latest_version": r[5],
            "last_author": r[6],
            "total_storage_bytes": r[7],
        }
        for r in rows
    ]
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


@app.get("/server/stats")
def server_stats(db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM projects)                         AS total_projects,
            (SELECT COUNT(*) FROM commits)                          AS total_versions,
            (SELECT COUNT(*) FROM assets)                           AS total_unique_blobs,
            (SELECT COALESCE(SUM(size_bytes), 0) FROM assets)       AS total_storage_bytes
    """)).fetchone()
    return {
        "total_projects":      row[0],
        "total_versions":      row[1],
        "total_unique_blobs":  row[2],
        "total_storage_bytes": row[3],
    }


@app.get("/projects/{project_name}/diff/{v1}/{v2}")
def diff_versions(project_name: str, v1: str, v2: str, db: Session = Depends(get_db)):
    logger.info("Diff: %s  %s → %s", project_name, v1, v2)

    commits = db.execute(text("""
        SELECT c.version_tag, c.id
        FROM commits c
        JOIN projects p ON c.project_id = p.id
        WHERE p.name = :pname AND c.version_tag = ANY(:tags)
    """), {"pname": project_name, "tags": [v1, v2]}).fetchall()

    tag_to_id = {row[0]: row[1] for row in commits}

    if v1 not in tag_to_id:
        raise HTTPException(status_code=404, detail=f"Version '{v1}' not found for project '{project_name}'.")
    if v2 not in tag_to_id:
        raise HTTPException(status_code=404, detail=f"Version '{v2}' not found for project '{project_name}'.")

    rows = db.execute(text("""
        WITH v1_files AS (
            SELECT pf.file_path, a.file_hash, a.size_bytes
            FROM project_files pf
            JOIN assets a ON pf.asset_id = a.id
            WHERE pf.commit_id = :cid1
        ),
        v2_files AS (
            SELECT pf.file_path, a.file_hash, a.size_bytes
            FROM project_files pf
            JOIN assets a ON pf.asset_id = a.id
            WHERE pf.commit_id = :cid2
        )
        SELECT
            COALESCE(v1_files.file_path, v2_files.file_path) AS path,
            v1_files.file_hash  AS hash1,
            v1_files.size_bytes AS size1,
            v2_files.file_hash  AS hash2,
            v2_files.size_bytes AS size2
        FROM v1_files
        FULL OUTER JOIN v2_files ON v1_files.file_path = v2_files.file_path
        ORDER BY path
    """), {"cid1": tag_to_id[v1], "cid2": tag_to_id[v2]}).fetchall()

    added, removed, changed, unchanged_count = [], [], [], 0
    for path, hash1, size1, hash2, size2 in rows:
        if hash1 is None:
            added.append({"path": path, "size": size2})
        elif hash2 is None:
            removed.append({"path": path, "size": size1})
        elif hash1 != hash2:
            changed.append({"path": path, "old_size": size1, "new_size": size2})
        else:
            unchanged_count += 1

    return {
        "v1": v1,
        "v2": v2,
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_count": unchanged_count,
    }


@app.patch("/projects/{project_name}")
def rename_project(project_name: str, request: RenameProjectRequest, db: Session = Depends(get_db)):
    logger.info("Renaming project: %s -> %s", project_name, request.new_name)
    try:
        result = db.execute(
            text("UPDATE projects SET name = :new_name WHERE name = :old_name RETURNING id"),
            {"new_name": request.new_name, "old_name": project_name}
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")
        db.commit()
        return {"status": "renamed", "old_name": project_name, "new_name": request.new_name}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Project '{request.new_name}' already exists.")
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error("Rename '%s' failed: %s", project_name, e)
        raise HTTPException(status_code=500, detail="Rename failed.")


@app.patch("/projects/{project_name}/description")
def update_description(project_name: str, request: UpdateDescriptionRequest, db: Session = Depends(get_db)):
    logger.info("Updating description for: %s", project_name)
    result = db.execute(
        text("UPDATE projects SET description = :desc WHERE name = :name RETURNING id"),
        {"desc": request.description, "name": project_name}
    )
    if not result.fetchone():
        db.rollback()
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")
    db.commit()
    return {"status": "updated", "project": project_name}


@app.delete("/projects/{project_name}")
def delete_project(
    project_name: str,
    request: DeleteProjectRequest,
    x_confirm_delete: Optional[str] = Header(None, alias="X-Confirm-Delete"),
    db: Session = Depends(get_db),
):
    _require_confirm_delete(x_confirm_delete)
    logger.info("Deleting project: %s (orphan cleanup: %s)", project_name, request.delete_orphan_files)

    project = db.execute(
        text("SELECT id FROM projects WHERE name = :name"),
        {"name": project_name}
    ).fetchone()

    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")

    project_id = project[0]

    # Collect orphan FIDs before any deletions (project_files still exist for the query)
    orphan_fids = []
    if request.delete_orphan_files:
        orphan_fids = [r[0] for r in db.execute(text("""
            SELECT a.seaweed_fid FROM assets a
            WHERE a.id IN (
                SELECT DISTINCT pf.asset_id FROM project_files pf
                JOIN commits c ON pf.commit_id = c.id
                WHERE c.project_id = :pid
            )
            AND a.id NOT IN (
                SELECT DISTINCT pf.asset_id FROM project_files pf
                JOIN commits c ON pf.commit_id = c.id
                WHERE c.project_id != :pid
            )
        """), {"pid": project_id}).fetchall()]

    # Delete project first — cascades to commits → project_files, freeing asset FK refs
    db.execute(text("DELETE FROM projects WHERE id = :pid"), {"pid": project_id})

    # Now safe to delete orphan assets (project_files no longer reference them)
    if orphan_fids:
        db.execute(
            text("DELETE FROM assets WHERE seaweed_fid = ANY(:fids)"),
            {"fids": orphan_fids}
        )

    db.commit()

    deleted_blobs = 0
    leaked_fids = []
    for fid in orphan_fids:
        try:
            requests.delete(f"{SEAWEED_FILER_URL}{fid}", timeout=5)
            deleted_blobs += 1
        except Exception as e:
            # DB record already gone — log the FID so it can be manually cleaned up
            logger.warning("Failed to delete blob %s from SeaweedFS: %s", fid, e)
            leaked_fids.append(fid)

    if leaked_fids:
        logger.warning(
            "Project '%s': %d blob(s) removed from DB but NOT from SeaweedFS (storage unavailable?). "
            "Leaked FIDs: %s",
            project_name, len(leaked_fids), leaked_fids,
        )

    logger.info("Project '%s' deleted. Orphan blobs removed: %d, leaked: %d",
                project_name, deleted_blobs, len(leaked_fids))
    return {
        "status": "deleted",
        "project": project_name,
        "orphan_blobs_deleted": deleted_blobs,
        "orphan_blobs_leaked": len(leaked_fids),
    }


@app.post("/admin/cleanup-orphans")
def cleanup_orphans(
    x_confirm_delete: Optional[str] = Header(None, alias="X-Confirm-Delete"),
    db: Session = Depends(get_db),
):
    """Delete asset records (and their SeaweedFS blobs) that are not referenced
    by any project_files row.

    A 24-hour grace window prevents racing with in-progress pushes: a file that
    was confirmed via /confirm_upload but whose /commit is still pending will not
    be collected until the next day.

    Safe to call repeatedly — idempotent.

    Requires the X-Confirm-Delete header so a stray script can't silently wipe
    orphaned blobs (auth is Phase 7; this is the interim guardrail).
    """
    _require_confirm_delete(x_confirm_delete)
    orphan_rows = db.execute(text("""
        SELECT id, seaweed_fid FROM assets
        WHERE id NOT IN (SELECT DISTINCT asset_id FROM project_files)
        AND created_at < NOW() - INTERVAL '24 hours'
    """)).fetchall()

    if not orphan_rows:
        logger.info("Orphan cleanup: nothing to remove.")
        return {"status": "ok", "assets_deleted": 0, "blobs_deleted": 0}

    orphan_ids  = [r[0] for r in orphan_rows]
    orphan_fids = [r[1] for r in orphan_rows]

    # Remove DB records first
    db.execute(text("DELETE FROM assets WHERE id = ANY(:ids)"), {"ids": orphan_ids})
    db.commit()
    logger.info("Orphan cleanup: removed %d asset record(s) from DB.", len(orphan_ids))

    # Remove blobs from SeaweedFS (best-effort; failures are logged but don't abort)
    blobs_deleted = 0
    for fid in orphan_fids:
        try:
            requests.delete(f"{SEAWEED_FILER_URL}{fid}", timeout=5)
            blobs_deleted += 1
        except Exception as e:
            logger.warning("Orphan cleanup: failed to delete blob %s: %s", fid, e)

    logger.info(
        "Orphan cleanup: deleted %d/%d blob(s) from SeaweedFS.",
        blobs_deleted, len(orphan_fids),
    )
    return {
        "status": "ok",
        "assets_deleted": len(orphan_ids),
        "blobs_deleted": blobs_deleted,
    }


# Run with: uv run uvicorn main:app --reload
