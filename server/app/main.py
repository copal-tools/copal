import requests
import os
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from database import get_db # Import our new connection tool

# Database Connection
# Default to localhost for local testing, but use Env Var for Docker
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:CHANGE_ME_IN_DOT_ENV@127.0.0.1:5432/asset_system")

# SeaweedFS Internal (API -> Master)
SEAWEED_MASTER_URL = os.getenv("SEAWEED_MASTER_URL", "http://127.0.0.1:9333")

# Public IP (What we send to the client)
# If running in Docker, this comes from the YAML. If local, defaults to your IP.
SERVER_PUBLIC_IP = os.getenv("PUBLIC_ACCESS_HOST", "192.168.178.161")





def get_upload_url(replication="000"):
    """
    Asks SeaweedFS for a file ID, then rewrites the URL to use the Public IP.
    """
    try:
        # 1. Ask SeaweedFS for a spot
        response = requests.get(f"{SEAWEED_MASTER_URL}/dir/assign?replication={replication}")
        data = response.json()
        
        # 2. Get the "Internal" URL (e.g., 172.24.0.2:8080)
        internal_url_str = data.get('publicUrl') or data.get('url')
        
        # 3. DEBUG LOGGING (Check your terminal for this!)
        print(f" [DEBUG] Seaweed gave us: {internal_url_str}")

        # 4. SWAP THE IP
        # We take the port from the internal URL (usually 8080)
        port = internal_url_str.split(":")[-1]
        
        # We force the URL to use your Server's Public IP
        corrected_url = f"http://{SERVER_PUBLIC_IP}:{port}/{data['fid']}"
        
        print(f" [DEBUG] We sent client: {corrected_url}")
        
        return {
            "fid": data["fid"],
            "upload_url": corrected_url
        }
    except Exception as e:
        print(f"Error talking to SeaweedFS: {e}")
        return None


app = FastAPI()

# --- DATA MODELS (The Contract) ---
# These define what the JSON looks like. 
# FastAPI validates this automatically.

class AssetEntry(BaseModel):
    path: str
    hash: str
    size: int

class HandshakeRequest(BaseModel):
    project_id: str
    client_manifest: List[AssetEntry]

class HandshakeResponse(BaseModel):
    required_files: List[str]  # Paths that need uploading
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
    files: List[AssetEntry] # The list of {path, hash, size}

# --- THE ENDPOINT ---
@app.post("/handshake", response_model=HandshakeResponse)
def handshake(request: HandshakeRequest, db: Session = Depends(get_db)):
    print(f"Incoming handshake for: {request.project_id}")
    
    # 1. Extract just the hashes from the client's request
    # Set comprehension for speed (removes duplicates instantly)
    client_hashes = {asset.hash for asset in request.client_manifest}
    
    if not client_hashes:
        return {"required_files": [], "message": "Manifest was empty."}

    # 2. Query the DB: "Select hashes that match this list"
    # We use a raw SQL query for maximum performance with thousands of files
    # Converting the set to a tuple for SQL syntax: ('hash1', 'hash2')
    query = text("SELECT file_hash FROM assets WHERE file_hash IN :hashes")
    
    # Run the query
    result = db.execute(query, {"hashes": tuple(client_hashes)})
    
    # 3. Create a set of "Known Hashes"
    known_hashes = {row[0] for row in result}
    
    # 4. Filter the list
    # If the hash is NOT in known_hashes, we need to upload it.
    files_to_upload = []
    skipped_count = 0
    
    for asset in request.client_manifest:
        if asset.hash in known_hashes:
            skipped_count += 1
        else:
            files_to_upload.append(asset.path)
            
    # 5. Summary
    msg = f"Checked {len(request.client_manifest)} files. Database found {skipped_count}. Need {len(files_to_upload)} new files."
    print(msg)
    
    return {
        "required_files": files_to_upload,
        "message": msg
    }

class UploadRequest(BaseModel):
    files: List[str] # List of file paths the client wants to upload

@app.post("/get_upload_urls")
def get_urls(request: UploadRequest):
    print(f"Generating upload tokens for {len(request.files)} files...")
    
    upload_map = {}
    
    for file_path in request.files:
        # Get a unique spot in SeaweedFS for each file
        # OPTIMIZATION: In the future, you can bundle small files, 
        # but for now, 1 file = 1 request is safer.
        assignment = get_upload_url()
        
        if assignment:
            upload_map[file_path] =  assignment
    
    return {"upload_map": upload_map}

@app.post("/confirm_upload")
def confirm_upload(request: ConfirmUploadRequest, db: Session = Depends(get_db)):
    print(f"Finalizing upload: {request.file_hash[:8]}... -> {request.seaweed_fid}")

    # SIMPLER STRATEGY: Raw SQL
    # This says: "Try to insert. If the hash already exists, do nothing (DO NOTHING)."
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
        print(f"DB Error: {e}")
        # Print the error to the terminal so we can see it!
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/commit")
def create_commit(request: CommitRequest, db: Session = Depends(get_db)):
    print(f"Creating commit '{request.version_tag}' for {request.project_id}...")
    
    # 1. GET PROJECT ID (Simple lookup, creating if missing for this demo)
    # In a real app, you'd fail if project doesn't exist.
    project = db.execute(text("SELECT id FROM projects WHERE name = :name"), {"name": request.project_id}).fetchone()
    
    if not project:
        # Create dummy project if it doesn't exist
        print(f"Project {request.project_id} not found, creating it...")
        result = db.execute(text("INSERT INTO projects (name) VALUES (:name) RETURNING id"), {"name": request.project_id})
        project_id = result.fetchone()[0]
        db.commit()
    else:
        project_id = project[0]

    # 2. CREATE THE COMMIT
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
    
    # 3. LINK FILES TO THIS COMMIT
    # We need to look up the ASSET_ID for every hash the client sent.
    # This is a bulk operation.
    
    # A. Get all asset IDs for these hashes
    file_map = {f.path: f.hash for f in request.files}
    unique_hashes = tuple(set(file_map.values()))
    
    if not unique_hashes:
         return {"status": "ok", "message": "Empty commit created."}

    asset_query = text("SELECT file_hash, id FROM assets WHERE file_hash IN :hashes")
    asset_rows = db.execute(asset_query, {"hashes": unique_hashes}).fetchall()
    
    # Map Hash -> Asset UUID
    hash_to_uuid = {row[0]: row[1] for row in asset_rows}
    
    # B. Insert rows into PROJECT_FILES
    # We prepare a list of values to insert
    links_to_create = []
    for f in request.files:
        if f.hash in hash_to_uuid:
            links_to_create.append({
                "cid": commit_id,
                "aid": hash_to_uuid[f.hash],
                "path": f.path
            })
            
    # Bulk Insert
    if links_to_create:
        db.execute(text("""
            INSERT INTO project_files (commit_id, asset_id, file_path)
            VALUES (:cid, :aid, :path)
        """), links_to_create)
        
    db.commit()
    print(f"✅ Commit {request.version_tag} created with {len(links_to_create)} files.")
    
    return {"commit_id": str(commit_id), "status": "success"}


@app.get("/checkout/{project_name}/{version_tag}")
def checkout_version(project_name: str, version_tag: str, db: Session = Depends(get_db)):
    print(f"Checkout request for {project_name} : {version_tag}")

    # 1. Find the Commit ID based on Project Name + Tag
    #    We join Projects -> Commits
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

    # 2. Get the file manifest
    #    We join Project_Files -> Assets to get paths and FIDs
    files_query = text("""
        SELECT pf.file_path, a.seaweed_fid, a.file_hash, a.size_bytes
        FROM project_files pf
        JOIN assets a ON pf.asset_id = a.id
        WHERE pf.commit_id = :cid
    """)
    files = db.execute(files_query, {"cid": commit_id}).fetchall()
    
    # 3. Format into a clean JSON list
    manifest = []
    for row in files:
        manifest.append({
            "path": row[0],
            "fid": row[1],
            "hash": row[2],
            "size": row[3]
        })
        
    return {
        "project": project_name,
        "version": version_tag,
        "file_count": len(manifest),
        "files": manifest
    }



# Run with: uv run uvicorn main:app --reload
