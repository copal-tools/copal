import logging
import os
import requests
from urllib.parse import urlparse
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List
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
SEAWEED_FILER_URL = f"{_master.scheme}://{_master.hostname}:8888"

# Public IP (What we send to the client)
# In Docker this comes from the compose env. Locally, defaults to your server IP.
SERVER_PUBLIC_IP = os.getenv("PUBLIC_ACCESS_HOST", "192.168.178.161")


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

    query = text("SELECT file_hash FROM assets WHERE file_hash IN :hashes")
    result = db.execute(query, {"hashes": tuple(client_hashes)})
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


@app.post("/commit")
def create_commit(request: CommitRequest, db: Session = Depends(get_db)):
    logger.info("Creating commit '%s' for project '%s'", request.version_tag, request.project_id)

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
    unique_hashes = tuple(set(file_map.values()))

    if unique_hashes:
        asset_rows = db.execute(
            text("SELECT file_hash, id FROM assets WHERE file_hash IN :hashes"),
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
            "auth": request.author
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
def checkout_version(project_name: str, version_tag: str, db: Session = Depends(get_db)):
    logger.info("Checkout: %s @ %s", project_name, version_tag)

    query = text("""
        SELECT c.id
        FROM commits c
        JOIN projects p ON c.project_id = p.id
        WHERE p.name = :pname AND c.version_tag = :vtag
    """)
    result = db.execute(query, {"pname": project_name, "vtag": version_tag}).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Project or Version not found")

    commit_id = result[0]

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


@app.get("/projects/{project_name}/metadata")
def get_project_metadata(project_name: str, db: Session = Depends(get_db)):
    logger.info("Fetching metadata for: %s", project_name)

    query_info = text("""
        SELECT p.id, p.created_at, c.id, c.version_tag, c.author_name, c.created_at, c.message
        FROM projects p
        JOIN commits c ON p.id = c.project_id
        WHERE p.name = :name
        ORDER BY c.created_at DESC
        LIMIT 1
    """)
    row = db.execute(query_info, {"name": project_name}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Project not found or has no versions")

    project_id, p_created, commit_id, tag, author, c_created, msg = row

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
        "latest_version": tag,
        "author": author,
        "authors": [r[0] for r in authors_rows],
        "updated_at": c_created.isoformat(),
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
def list_projects(db: Session = Depends(get_db)):
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
                ORDER BY c2.created_at DESC LIMIT 1) AS last_author
        FROM projects p
        LEFT JOIN commits c ON c.project_id = p.id
        GROUP BY p.id
        ORDER BY MAX(c.created_at) DESC NULLS LAST
    """)).fetchall()

    return [
        {
            "name": r[0],
            "created_at": r[1].isoformat() if r[1] else None,
            "version_count": r[2],
            "author_count": r[3],
            "last_push": r[4].isoformat() if r[4] else None,
            "latest_version": r[5],
            "last_author": r[6],
        }
        for r in rows
    ]


@app.delete("/projects/{project_name}")
def delete_project(project_name: str, request: DeleteProjectRequest, db: Session = Depends(get_db)):
    logger.info("Deleting project: %s (orphan cleanup: %s)", project_name, request.delete_orphan_files)

    project = db.execute(
        text("SELECT id FROM projects WHERE name = :name"),
        {"name": project_name}
    ).fetchone()

    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")

    project_id = project[0]

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

        if orphan_fids:
            db.execute(
                text("DELETE FROM assets WHERE seaweed_fid IN :fids"),
                {"fids": tuple(orphan_fids)}
            )

    db.execute(text("DELETE FROM projects WHERE id = :pid"), {"pid": project_id})
    db.commit()

    deleted_blobs = 0
    for fid in orphan_fids:
        try:
            requests.delete(f"{SEAWEED_FILER_URL}{fid}", timeout=5)
            deleted_blobs += 1
        except Exception:
            pass

    logger.info("Project '%s' deleted. Orphan blobs removed: %d", project_name, deleted_blobs)
    return {"status": "deleted", "project": project_name, "orphan_blobs_deleted": deleted_blobs}


# Run with: uv run uvicorn main:app --reload
