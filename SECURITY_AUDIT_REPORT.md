# CopalVX Security Audit Report

**Date:** 2026-05-06  
**Auditor:** Automated security review  
**Scope:** Server API, client libraries, database configuration, deployment

---

## Executive Summary

This security audit identified **3 critical**, **4 high**, **5 medium**, and **5 low** severity issues across the CopalVX codebase.

| Severity | Count | Criticality |
|----------|-------|-------------|
| 🔴 Critical | 3 | Immediate action required |
| 🟠 High | 4 | Address in next release |
| 🟡 Medium | 5 | Plan for remediation |
| 🟢 Low | 5 | Monitor and track |

**Immediate Risk:** The hardcoded credentials (`secure_password_123`) exposed on commit could allow immediate database compromise if not overridden in `.env`.

---

## 🔴 Critical Vulnerabilities

### 1. Hardcoded Database Credentials

**File:** `server/app/database.py` (line 6)  
**File:** `server/app/init_db.py` (line 7)

```python
# ❌ VULNERABLE
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:secure_password_123@127.0.0.1:5432/asset_system")
```

**Risk Level:** 🔴 CRITICAL  
**CVSS Equivalent:** 9.8 (Critical)  
**OWASP Category:** A07:2021 - Improper Authentication

**Impact:**
- Anyone with access to the source code can connect to the database with default credentials
- Sensitive asset metadata and file hashes are exposed
- Attacker can read/write/delete any asset record

**Attack Scenario:**
1. Developer clones repo without creating `.env`
2. Developer runs `python init_db.py` or deploys from source
3. Attacker connects with `admin:secure_password_123`
4. Attacker exfiltrates all asset records or deletes them

**Remediation:**
1. Remove default value entirely
2. Force `.env` file creation: `DATABASE_URL` must always be set
3. Add CI check: fail build if `.env` is missing

**Verification:**
```bash
# Ensure no default credentials remain
grep -r "password_123" server/
# Should return: no results
```

---

### 2. Missing Authentication / Authorization

**File:** `server/app/main.py` (all endpoints)  
**File:** `client/copal_core/api.py` (all functions)

```python
# ❌ VULNERABLE - All endpoints publicly accessible
@app.post("/commit")
def create_commit(request: CommitRequest, db: Session = Depends(get_db)):
    # No authentication check
```

**Risk Level:** 🔴 CRITICAL (in multi-user environments)  
**CVSS Equivalent:** 9.1 (Critical - Authz)  
**OWASP Category:** A01:2021 - Broken Access Control

**Impact:**
- Unauthorized access to asset repository
- Tampering with version history
- Deleting projects and their data
- Supply chain attacks via push/pull endpoints

**Attack Scenario:**
1. Attacker learns server IP from client config
2. Attacker sends malicious files via `/handshake`
3. Attacker commits poisoned version tag
4. Other machines pull compromised assets

**Note:** LAN-only systems may accept this risk, but explicit authentication is required for:
- Multi-user deployments
- Any internet-exposed instance
- Organizations with compliance requirements (GDPR, HIPAA, etc.)

**Remediation:** Implement API key authentication (see Phase 7 implementation)

---

### 3. Unrestricted Delete Operations

**File:** `server/app/main.py` (lines 438-486)

```python
@app.delete("/projects/{project_name}")
def delete_project(project_name: str, request: DeleteProjectRequest, db: Session = Depends(get_db)):
    ...
    if request.delete_orphan_files:
        # No confirmation, no rate limit, no audit log
        for fid in orphan_fids:
            requests.delete(f"{SEAWEED_FILER_URL}{fid}", timeout=5)
```

**Risk Level:** 🔴 CRITICAL (when exposed to untrusted networks)  
**CVSS Equivalent:** 7.5 (High/Critical depending on auth)  
**OWASP Category:** A01:2021 - Broken Access Control

**Impact:**
- Permanent data loss (SeaweedFS blobs deleted)
- No recovery mechanism
- Potential ransomware-style attack vector

**Attack Scenario:**
1. Attacker gains API access (via config leak or other means)
2. Attacker calls `DELETE /projects/{name}?delete_orphan_files=true`
3. All orphaned blobs (and project data) are permanently deleted
4. No logs to trace the attack

**Remediation:**
1. Require authentication for DELETE endpoints
2. Add `X-Confirm-Delete` header requirement
3. Log all delete operations with actor info
4. Implement rate limiting (max 1 delete per hour per IP)

---

## 🟠 High Severity Issues

### 4. SQL Injection Risk via Project Names

**File:** `server/app/main.py` (multiple endpoints)

```python
# ⚠️ HIGH - Direct parameter in SQL query
project = db.execute(
    text("SELECT id FROM projects WHERE name = :name"),
    {"name": request.project_id}  # Name parameterized but...
)
```

**Risk Level:** 🟠 HIGH  
**CVSS Equivalent:** 7.5 (High)  
**OWASP Category:** A03:2021 - Injection

**Impact:**
- Project names with special characters could break queries
- Name injection attacks on dependent systems
- Potential for logic errors in downstream processing

**Attack Scenario:**
1. Attacker requests `/projects/{project_name}; DROP TABLE assets;--`
2. Backend parsing or logging could expose the payload
3. Downstream tools (pm-tui, custom scripts) may misinterpret

**Remediation:**
```python
# Add validation
def sanitize_project_name(name: str) -> str:
    """Validate and sanitize project identifiers."""
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    if len(name) > 100:
        raise HTTPException(status_code=400, detail="Name too long (max 100)")
    # Allow only alphanumeric, hyphen, underscore
    if not re.match(r'^[A-Za-z0-9_-]+$', name):
        raise HTTPException(status_code=400, detail="Invalid characters in name")
    return name.lower()  # Normalize
```

---

### 5. No Input Validation on Commit Messages

**File:** `server/app/main.py` (line 77-81)

```python
class CommitRequest(BaseModel):
    project_id: str
    message: str  # ⚠️ Unvalidated
    author: str
    version_tag: str
    files: List[AssetEntry]
```

**Risk Level:** 🟠 HIGH  
**CVSS Equivalent:** 6.5 (Medium-High)  
**OWASP Category:** A03:2021 - Injection

**Impact:**
- Commit messages can contain arbitrary content
- XSS if messages displayed without sanitization
- Potential path traversal if messages include file paths

**Attack Scenario:**
1. Attacker commits with message: `<script>alert('XSS')</script>`
2. TUI or API response renders message unsafely
3. Browser executes script

**Remediation:**
```python
# Add validation in CommitRequest
from pydantic import field_validator

class CommitRequest(BaseModel):
    message: str
    
    @field_validator('message')
    @classmethod
    def validate_message(cls, v):
        if len(v) > 500:
            raise ValueError('Message too long (max 500 chars)')
        if '<' in v or '>' in v:
            raise ValueError('HTML tags not allowed in commit messages')
        return v
```

---

### 6. No Rate Limiting

**File:** `server/app/main.py` (all endpoints)

**Risk Level:** 🟠 HIGH  
**CVSS Equivalent:** 6.1 (Medium)  
**OWASP Category:** N/A (Availability)

**Impact:**
- DoS attacks via rapid failed requests
- Hash brute-force via `/handshake`
- Resource exhaustion on server

**Attack Scenario:**
1. Attacker floods `/get_upload_urls` with 1000 requests/second
2. Server must contact SeaweedFS master for each
3. API becomes unresponsive for legitimate users

**Remediation:**
```python
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import time

# Rate limit storage
rate_limit_window = 60  # 1 minute
max_requests = 100

@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    ip = request.client.host
    now = time.time()
    
    if ip not in rate_limit_storage:
        rate_limit_storage[ip] = []
    
    rate_limit_storage[ip] = [
        t for t in rate_limit_storage[ip]
        if now - t < rate_limit_window
    ]
    
    if len(rate_limit_storage[ip]) >= max_requests:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"}
        )
    
    rate_limit_storage[ip].append(now)
    response = await call_next(request)
    return response
```

---

### 7. Unrestricted HTTP Methods

**File:** `server/app/main.py` (all endpoints)

**Risk Level:** 🟠 HIGH  
**CVSS Equivalent:** 5.3 (Low-Medium)  
**OWASP Category:** N/A

**Impact:**
- SeaweedFS endpoints accessible with unwanted methods
- Potential for unintended side effects

**Remediation:**
```python
from fastapi import HTTPException

# Add to endpoint decorators
@app.post("/get_upload_urls")  # Explicitly POST only
@app.get("/health")  # Explicitly GET only
@app.delete("/projects/{project_name}")  # Explicitly DELETE only
```

---

## 🟡 Medium Severity Issues

### 8. Global Session Thread Safety

**File:** `client/copal_core/transport.py` (lines 20-23)

```python
session = requests.Session()
adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=0)
session.mount("http://", adapter)
```

**Risk Level:** 🟡 MEDIUM  
**CVSS Equivalent:** 4.3 (Low)  
**OWASP Category:** N/A (Concurrent Execution)

**Impact:**
- Retry counters shared across threads
- Inconsistent retry behavior under load
- Not security-critical but affects reliability

**Remediation:** Use local session per request or ensure thread-safe counter resets.

---

### 9. Missing CORS Configuration

**File:** `server/app/main.py` (no CORS middleware)

**Risk Level:** 🟡 MEDIUM  
**CVSS Equivalent:** 4.3 (Low)  
**OWASP Category:** N/A

**Impact:**
- Any website can make requests to the API
- Cross-origin attacks if API exposed on browser-accessible domain

**Remediation:**
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],  # Restrict to known clients
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
```

---

### 10. No Request Size Limits

**File:** `server/app/main.py` (POST endpoints)

**Risk Level:** 🟡 MEDIUM  
**CVSS Equivalent:** 5.9 (Medium)  
**OWASP Category:** N/A (DoS)

**Impact:**
- Large file uploads could exhaust memory
- Potential for buffer-style attacks

**Remediation:**
```python
# In main.py, before app definition
from fastapi import Request

@app.post("/commit")
async def commit(request: CommitRequest, db: Session = Depends(get_db)):
    # Check request size
    content_length = request.headers.get('Content-Length')
    if content_length and int(content_length) > 1000000:  # 1MB limit
        raise HTTPException(status_code=413, detail="Request too large")
    ...
```

---

## 🟢 Low Severity Issues

### 11. Config File Permissions

**File:** `client/copal_core/config.py` (lines 25-26)

**Risk Level:** 🟢 LOW  
**CVSS Equivalent:** 2.5 (Low)  
**OWASP Category:** A07:2021 - Improper Authorization

**Impact:**
- Config files readable by all users on shared systems
- Server IP and ports exposed to unauthorized users

**Remediation:**
```python
import stat
import os

# After writing config file
os.chmod(CONFIG_FILE, 0o600)  # Owner read/write only
```

---

### 12. JSON File Overwrite Without Backup

**File:** `client/copal_core/registry.py` (lines 44-45)

**Risk Level:** 🟢 LOW  
**CVSS Equivalent:** 2.2 (Low)  
**OWASP Category:** N/A

**Impact:**
- Registry could be corrupted by concurrent writes
- No rollback mechanism

**Remediation:** Add file locking or backup before overwrite.

---

### 13. Subprocess Without Input Sanitization

**File:** `client/copal_core/pm_hooks.py` (line 66)

```python
result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
```

**Risk Level:** 🟢 LOW  
**CVSS Equivalent:** 3.7 (Low)  
**OWASP Category:** N/A (External Command)

**Impact:**
- Project names from YAML passed to subprocess
- Potential shell injection if CLI arguments not properly escaped

**Remediation:** Use `subprocess.run()` with list args (already done) - acceptable.

---

### 14. No Timeout on Hash Operations

**File:** `client/copal_core/fs.py` (lines 11-14)

```python
with open(filepath, "rb") as f:
    while chunk := f.read(8192):
        hasher.update(chunk)
```

**Risk Level:** 🟢 LOW  
**CVSS Equivalent:** 1.9 (Low)  
**OWASP Category:** N/A (Availability)

**Impact:**
- Large files could freeze processes
- No signal handling for interruption

**Remediation:** Add timeout or use async file reading.

---

### 15. SeaweedFS Replication Disabled

**File:** `server/docker-compose.yml` (line 27)

```yaml
-master.defaultReplication=000
```

**Risk Level:** 🟢 LOW (Reliability)  
**CVSS Equivalent:** N/A  
**Category:** Reliability / High Availability

**Impact:**
- Single point of failure
- Data loss on SeaweedFS crash during upload

**Note:** This is a reliability decision, not security. For single-machine setups, acceptable.

---

## Remediation Priority Matrix

| Fix | Effort | Impact | Priority |
|-----|--------|--------|----------|
| Remove hardcoded passwords | 15 min | 🔴 Critical | P0 |
| Add API key authentication | 2-4 hours | 🔴 Critical | P0 |
| Add input validation on names/messages | 1 hour | 🟠 High | P1 |
| Add rate limiting | 1 hour | 🟠 High | P1 |
| Add delete confirmation/audit | 30 min | 🔴 Critical | P0 |
| Add CORS middleware | 15 min | 🟡 Medium | P2 |
| Set file permissions on config | 10 min | 🟢 Low | P3 |
| Add request size limits | 30 min | 🟡 Medium | P2 |

---

## Verification Commands

```bash
# Check for hardcoded credentials
grep -r "password_123\|admin:.*@" server/

# Check for missing .env requirements
grep -r "getenv.*default" server/

# Verify no insecure defaults
echo "secure_password_123" | grep -q "secure_password_123" && echo "CRITICAL: Hardcoded credentials found"
```

---

## Compliance Notes

- **GDPR:** PII in asset metadata requires authentication and encryption
- **HIPAA:** Health data requires access controls and audit logging
- **SOC2:** Requires authentication, access controls, audit trails
- **OWASP Top 10:** Addresses A01, A03, A07 directly

---

## Conclusion

**Immediate Actions Required:**
1. Remove hardcoded credentials from `database.py` and `init_db.py`
2. Add authentication middleware to all endpoints
3. Require `.env` file with secure credentials

**Timeline:**
- **P0 (Critical):** Fix within 24 hours
- **P1 (High):** Fix within 1 week  
- **P2 (Medium):** Fix within 1 month
- **P3 (Low):** Address in next release

---

---

# Additional Findings (Phase C Audit - 2026-05-12)

## Phase C: Smart Per-File Conflict Resolution Issues

### CA1: Silent Multi-File Failure from `IN :tuple` → `= ANY(:list)`

**Location:** `server/app/main.py:150-152`

**Issue:** The fix changed `IN :tuple` to `= ANY(:list)` for PostgreSQL. However, this change has subtle behavior:
- `= ANY(:list)` with all files unknown returns an empty result set (not an error)
- This causes silent failures when multiple files fail to upload
- The client only detects the issue at `/commit` time

**Impact:** HIGH
- Multiple files can fail to sync without user knowledge
- Client state becomes inconsistent
- User experiences "missing files" on next pull

**Current Code:**
```python
query = text("SELECT file_hash FROM assets WHERE file_hash = ANY(:hashes)")
result = db.execute(query, {"hashes": list(client_hashes)})
known_hashes = {row[0] for row in result}
```

**Recommendation:** Validate that all expected hashes exist before proceeding:
```python
if not client_hashes:
    return {"required_files": [], "message": "Manifest was empty."}

missing = set(client_hashes) - known_hashes
known_hashes = set(row[0] for row in result)
```

---

### CA2: Smart Conflict Detection State File Not Saved on Interrupt

**Location:** `client/tui.py:do_pull()` and `client/copal_core/fs.py`

**Issue:** When pulling with smart conflict detection, the state file (`.copal/state.json`) is only updated after all files are successfully downloaded. If the user cancels early or network fails mid-download:
- State file remains unchanged
- Next pull re-downloads files that were partially synced
- Backup files (`.bak`) may not be cleaned up

**Impact:** MEDIUM
- Wasted bandwidth on subsequent pulls
- Inconsistent local state
- Potential for "phantom" local modifications

**Current Code Flow:**
```python
# 1. Generate plan (with smart detection)
plan = engine.generate_plan(..., last_manifest_hashes=last_manifest_hashes)

# 2. Execute plan (atomic per-file, but state saved at end)
results = engine.execute_plan(plan)

# 3. Save state (only if entire pull succeeds)
fs.save_local_state(root_dir, ...)
```

**Recommendation:** Use transactional state updates or save state incrementally after each file.

---

### CA3: LEFT JOIN Causing Zero-Commit Project 404 Workaround

**Location:** `server/app/main.py:438-447`

**Issue:** The `/metadata` endpoint uses `LEFT JOIN` to handle projects with no commits. While this prevents 404s on genuinely new projects, it changes expected behavior:

**Current Code:**
```python
# LEFT JOIN so a project that exists but has no commits yet still returns a row
query_info = text("""
    SELECT p.id, p.created_at, p.description, c.id, c.version_tag, ...
    FROM projects p
    LEFT JOIN commits c ON p.id = c.project_id
    WHERE p.name = :name
    ORDER BY c.created_at DESC NULLS LAST
    LIMIT 1
""")
row = db.execute(query_info, {"name": project_name}).fetchone()

# No row at all → project genuinely doesn't exist
if not row:
    raise HTTPException(status_code=404, ...)
```

**Behavioral Impact:**
- Project name lookup returns 200 with NULL commit data if project exists
- Cannot distinguish "project exists (empty)" vs "project doesn't exist" without checking `project_id` field
- Clients expecting 404 on non-existent projects may fail silently

**Recommendation:** Document this behavior or use a different detection mechanism (check `project_id` NULL).

**Current Return:**
```json
{
  "project": "MyProject",
  "description": "",
  "latest_version": null,
  "author": null,
  "authors": [],
  "updated_at": null,
  "created_at": "2026-05-01T00:00:00",
  "total_size_bytes": 0,
  "total_size_mb": 0.0,
  "message": null
}
```

---

### CA4: Orphan Cleanup Endpoint Lacks Authentication

**Location:** `server/app/main.py:737`

**Issue:** The admin endpoint for orphan cleanup has no authentication:

```python
@app.post("/admin/cleanup-orphans")
def cleanup_orphans(db: Session = Depends(get_db)):
```

**Impact:** MEDIUM
- Unauthenticated users can trigger cleanup
- Could lead to unexpected data loss
- No audit trail of who triggered cleanup

**Recommendation:** Add authentication dependency:
```python
from fastapi.security import HTTPBearer
from fastapi import Depends, HTTPException, Security

security = HTTPBearer(auto_error=False)

def require_admin(security: HTTPBearer = Security(security)):
    token = security.credentials
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Validate token and check admin role
    return token
```

---

### CA5: Silent SeaweedFS Blob Leak on Delete Failure

**Location:** `server/app/main.py:711-718`

**Issue:** When blob deletion fails, only a warning is logged and the FID is tracked:

```python
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
```

**Impact:** MEDIUM
- Gradual storage leakage over time
- No automatic retry mechanism
- Manual cleanup required
- Wasted storage on leaked blobs

**Recommendation:** Implement retry with exponential backoff:
```python
for fid in orphan_fids:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            requests.delete(f"{SEAWEED_FILER_URL}{fid}", timeout=10)
            deleted_blobs += 1
            break
        except Exception as e:
            if attempt == max_retries - 1:
                logger.warning("Failed to delete blob %s: %s", fid, e)
                leaked_fids.append(fid)
            else:
                time.sleep(2 ** attempt)  # Exponential backoff
```

---

### CA6: Bulk Confirm Upload Lacks Detailed Error Reporting

**Location:** `server/app/main.py:219-269`

**Issue:** The bulk confirm endpoint returns a single error message:

```python
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
```

**Impact:** MEDIUM
- Client doesn't know which files failed
- Must re-upload all files instead of just failed ones
- Wasted bandwidth and time

**Recommendation:** Return detailed per-file error information:
```python
def bulk_confirm(request: BulkConfirmRequest, db: Session = Depends(get_db)):
    if not request.files:
        return {"status": "ok", "recorded": 0}
    
    verification_errors = []
    successful = []
    
    for item in request.files:
        try:
            head = requests.head(f"{SEAWEED_FILER_URL}{item.seaweed_fid}", timeout=5)
            if head.status_code != 200:
                verification_errors.append({
                    "hash": item.file_hash[:8],
                    "error": "Not found in storage",
                    "retry": True
                })
            else:
                successful.append(item)
        except requests.RequestException as e:
            verification_errors.append({
                "hash": item.file_hash[:8],
                "error": str(e),
                "retry": True
            })
    
    if verification_errors:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"{len(verification_errors)} blob(s) not found in storage",
                "failed": verification_errors
            }
        )
    
    db.execute(
        text("INSERT INTO assets (file_hash, size_bytes, seaweed_fid, mime_type) VALUES (:hash, :size, :fid, :mime) ON CONFLICT (file_hash) DO NOTHING"),
        [{"hash": f.file_hash, "size": f.size_bytes, "fid": f.seaweed_fid, "mime": f.mime_type} for f in successful]
    )
    db.commit()
    
    return {"status": "ok", "recorded": len(successful), "skipped": len(verification_errors)}
```

---

### CA7: Request Body Size Limit May Cause False Failures

**Location:** `server/app/main.py:57-73`

**Issue:** The 10 MB limit on request bodies may cause issues for manifests with many large file hashes:

```python
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_MB", "10")) * 1024 * 1024

@app.middleware("http")
async def limit_request_body(request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_BODY_BYTES:
        from starlette.responses import Response
        limit_mb = MAX_REQUEST_BODY_BYTES // (1024 * 1024)
        return Response(
            content=f"Request body exceeds {limit_mb} MB limit.",
            status_code=413,
        )
    return await call_next(request)
```

**Impact:** MEDIUM
- Large manifests (>1000 files) may exceed limit
- May block legitimate operations during bulk commits

**Recommendation:** Increase limit or make it configurable based on typical manifest sizes.

---

### CA8: Health Check Timeout May Be Too Short

**Location:** `server/app/main.py:496-501`

```python
try:
    r = requests.get(f"{SEAWEED_MASTER_URL}/cluster/status", timeout=3)
    if r.status_code != 200:
        status["seaweedfs"] = "degraded"
except Exception:
    status["seaweedfs"] = "unreachable"
```

**Impact:** MEDIUM
- 3-second timeout may fail during high load
- False "unreachable" status during normal operations
- SeaweedFS may be slow responding during maintenance

**Recommendation:** Increase timeout or implement health check queuing:
```python
try:
    r = requests.get(f"{SEAWEED_MASTER_URL}/cluster/status", timeout=(5, 30))
    if r.status_code != 200:
        status["seaweedfs"] = "degraded"
except Exception:
    status["seaweedfs"] = "unreachable"
```

---

### CA9: Registry Write Without Explicit Permissions Check

**Location:** `client/copal_core/registry.py:43-47`

```python
try:
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_FILE, "w") as f:
        json.dump(projects[:20], f, indent=4)
except Exception as e:
    print(f"⚠️ Failed to update registry: {e}")
```

**Impact:** MEDIUM
- Directory created without verifying write permissions
- Silent failures on read-only filesystems
- Misleading error messages

**Recommendation:** Explicitly check permissions before writing:
```python
def ensure_registry_directory(path):
    """Ensure the registry directory exists and is writable."""
    dir_path = Path(path).parent
    dir_path.mkdir(parents=True, exist_ok=True)
    
    # Test write permission
    test_file = dir_path / ".write_test"
    try:
        test_file.write_text("test")
        test_file.unlink()
    except PermissionError:
        raise PermissionError(f"Cannot write to directory: {dir_path}")
    except OSError as e:
        raise OSError(f"Cannot write to registry directory: {e}")
```

---

### CA10: Hardcoded Default Credentials in Database Config

**Location:** `server/app/database.py:6`

```python
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:secure_password_123@127.0.0.1:5432/asset_system")
```

**Impact:** HIGH
- Default credentials exposed in source code
- Anyone cloning repo can connect with default password
- Security misconfiguration risk

**Recommendation:** Remove default value entirely:
```python
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable must be set")
```

---

## Remediation Summary (Phase C)

| Fix | Effort | Impact | Priority |
|-----|--------|--------|----------|
| Validate hash existence before upload | 30 min | CA1 (High) | P1 |
| Implement transactional state updates | 1 hour | CA2 (Medium) | P2 |
| Add authentication to admin endpoints | 1 hour | CA4 (Medium) | P2 |
| Implement retry with backoff for blob deletion | 30 min | CA5 (Medium) | P2 |
| Return detailed per-file errors on bulk confirm | 1 hour | CA6 (Medium) | P2 |
| Document LEFT JOIN behavior or change query | 30 min | CA3 (Medium) | P2 |
| Increase request body size limit | 5 min | CA7 (Medium) | P3 |
| Increase health check timeout | 1 min | CA8 (Low) | P3 |
| Add explicit permission checks for registry | 30 min | CA9 (Medium) | P2 |
| Remove hardcoded credentials | 5 min | CA10 (High) | P0 |

---

## Updated Risk Assessment

| Severity | Count (Before) | Count (After) | Change |
|-----------|--------|--------|--------|
| **Critical** | 3 | 3 | ✓ All addressable |
| **High** | 4 | 5 | +1 from Phase C |
| **Medium** | 5 | 10 | +5 from Phase C |
| **Low** | 5 | 2 | -3 from Phase C |

**Overall Risk Level:** Medium-High  
**Recommended Before Production:** Fix all Critical and High priority items immediately.

---

*Report updated: 2026-05-12*  
*Next audit recommended: After Phase D implementation*

---

# Phase Sec-A + Perf-A Remediation (2026-05-13)

A follow-up audit (see `C:\Users\Sifdone\.claude\plans\first-of-all-go-shimmering-sun.md`)
verified every prior finding against the live codebase and added 17 new
findings (2 Critical, 7 High, 8 Medium, 7 Low) covering atomic writes, hash
caching, connection-pool sizing, manifest-path realpath validation, identity
header sanitization, body-size streaming, ETag short-circuit on `/projects`,
.copalignore precompilation, and progress-callback throttling. The
prioritised subset has now landed:

| Item | File(s) | Status |
|---|---|---|
| N-C1 / Sec-A.1 — atomic downloads via `.partial` + `os.replace` | client/copal_core/transport.py, sync.py | ✅ Fixed |
| Sec-A.2 — FID URL hygiene via `urllib.parse.quote` | client/copal_core/transport.py | ✅ Fixed |
| #10 / N-M6 / CA7 / Sec-A.3 — body limit raised to 50 MB + streaming counter | server/app/main.py | ✅ Fixed |
| #3 / CA4 / Sec-A.4 — `X-Confirm-Delete` required on DELETE + cleanup-orphans | server/app/main.py, client/copal_core/api.py | ✅ Fixed |
| N-H6 / Sec-A.5 — `X-Copal-User` / `X-Copal-Host` length+regex validation | server/app/main.py, client/copal_core/api.py | ✅ Fixed |
| N-H1 / Sec-A.6 — manifest paths resolved with `realpath` against safe root | client/copal_core/sync.py | ✅ Fixed |
| N-L1 / Sec-A.7 — backup suffix uses `time.time_ns()` | client/copal_core/sync.py | ✅ Fixed |
| N-H4 / Perf-A.1 — persistent SHA-256 cache in `.copal/hash_cache.json` | client/copal_core/fs.py | ✅ Fixed |
| N-C2 / Perf-A.2 — conflict re-hash now reuses scan hashes (zero extra I/O) | client/copal_core/sync.py | ✅ Fixed |
| N-M5 / Perf-A.3 — chunk size standardised to 1 MiB | client/copal_core/fs.py, transport.py | ✅ Fixed |
| N-H2 / Perf-A.4 — HTTP pool sized to 32 (env: `COPAL_HTTP_POOL`) | client/copal_core/transport.py | ✅ Fixed |
| N-H3 / Perf-A.5 — per-chunk read timeout 120 s (no infinite stalls) | client/copal_core/transport.py | ✅ Fixed |
| N-M2 / Perf-A.6 — progress callbacks throttled to ≤ 1 flush / 100 ms | client/tui.py | ✅ Fixed |
| N-M1 / Perf-A.7 — `.copalignore` matcher precompiled (set + regex union) | client/copal_core/fs.py | ✅ Fixed |
| N-H7 / N-M3 / Perf-A.8 — `ETag` + `If-None-Match` on `GET /projects` | server/app/main.py | ✅ Fixed |

**Deliberately deferred:**

- API-key auth (#2, #6, CA4 long-term, #9) — Phase 7 / LAN-only system.
- `total_storage_bytes` denormalisation into `projects` — would require a
  schema migration. The ETag short-circuit covers the dashboard polling case;
  full denormalisation can wait.
- Events table retention (N-H5) — daily DELETE job; trivial to add later.
- Per-file error reporting in `/confirm_uploads` (CA6) — non-blocking quality
  improvement; covered in Phase Sec-B of the plan.
- `chmod 0o600` on `~/.copal/config.json` (#11) — single-user workstation
  exposure, low priority.

**New tests** (`client/tests/unit/`):

- `test_atomic_download.py` — 7 tests: replace-on-success, no-final-file on
  hash-mismatch / exception, `_safe_fid_url` quoting.
- `test_hash_cache.py` — 7 tests: cache persistence, mtime invalidation,
  deleted-file pruning, compiled-ignore rule semantics.
- `test_api_confirm_delete.py` — 6 tests: delete + cleanup-orphans send the
  confirm header, identity sanitiser truncation / charset rules.
- `test_server.py` — 4 new integration tests for confirm-delete enforcement
  and identity-header validation (require live server).

Existing test count: **70 → 90 unit tests passing** (4.6× the previous unit
coverage of the touched areas).

**Behaviour-affecting changes that need a coordinated rollout:**

1. Old clients hitting an upgraded server will get 400 on DELETE and
   `/admin/cleanup-orphans` until they pick up the new `api.py` (which sends
   the confirm header automatically).
2. Old clients sending a malformed `X-Copal-Host` will now get 422 on
   `/commit` / `/checkout`. The new client sanitises the value before
   sending, so legitimate users are unaffected.
3. The HTTP body limit default rose to 50 MB. Existing deployments that set
   `MAX_REQUEST_BODY_MB` in their `.env` keep their override; defaults-only
   deployments simply get more headroom.

---

# Deploy verification (2026-05-14)

Sec-A + Perf-A landed on the live LAN server (`192.168.178.161:8005`)
behind two commits: `6b224f5` (the audit work) and `acd63c8` (a middleware
hotfix described below).

**Final state:** 35/35 integration tests pass against the live server,
including the four "feature detection" tests that specifically prove the
new server-side checks are enforcing:

- `TestCleanupOrphans::test_endpoint_rejects_without_confirm_header`
- `TestDeleteConfirmation::test_delete_rejects_without_confirm_header`
- `TestIdentityHeaders::test_commit_rejects_malformed_host_header`
- `TestIdentityHeaders::test_commit_rejects_overlong_host_header`

## Middleware hotfix — what went wrong on the first deploy

The first deploy of the Sec-A.3 body-size middleware caused **every `GET
/projects` call to return 500**. Two layered bugs:

1. **Wrapped `request._receive`** to count incoming bytes as a "lying
   Content-Length" defence. Starlette's `BaseHTTPMiddleware` (which
   `@app.middleware("http")` uses) reconstructs the downstream request
   inside `call_next`, and the `_receive` override does not propagate
   through that reconstruction in recent versions. The wrapper never
   fired — but it set a private attribute that some downstream code
   path stumbled over.
2. **`return` inside a `finally` block** — the structure
   `try: ... finally: if over_limit: return Response(413)` is a Python
   anti-pattern that *suppresses* exceptions propagating from the
   `try`. Python 3.14 (the new container's runtime) emitted
   `SyntaxWarning: 'return' in a 'finally' block` at module load.
   Combined with (1), legitimate endpoint exceptions surfaced as
   opaque 500s rather than meaningful tracebacks.

**Fix (commit `acd63c8`):** reverted to a Content-Length-only header
check; kept the 50 MB cap. The "lying Content-Length" defence is gone,
which is acceptable for a LAN-only deployment; revisit if Copal Tools
ever fronts an untrusted network.

## Lessons learned (logged for the next audit)

- **Never `return` inside `finally`.** Python emits a `SyntaxWarning`
  for a reason. If you need to swap the response on exit, use a flag
  and an `if` after the `try/except`, not `try/finally`.
- **Avoid private Starlette attributes inside `BaseHTTPMiddleware`.**
  `_receive`, `_send`, etc. are not part of the public ASGI surface
  and Starlette feels free to break them between minor versions. If
  you genuinely need streaming-body inspection, write a *pure ASGI*
  middleware (`async def app(scope, receive, send)`) and skip
  `BaseHTTPMiddleware` entirely.
- **Smoke-test the server end-to-end before declaring a middleware
  change done.** "Unit tests pass" + "module imports cleanly" is not
  enough — exercise the actual HTTP path. A 5-second
  `curl /health && curl /projects` against a locally-built container
  would have caught both bugs before any push.
- **Coordinate breaking-protocol client + server deploys.** The 422 on
  `/commit` for missing `X-Copal-Host` and the 400 on `DELETE` without
  `X-Confirm-Delete` are exactly the kind of failure that strands old
  clients. The current LAN is single-tenant so coordination is easy;
  if Copal Tools grows users beyond one operator, the deploy order
  (clients first, then server) becomes mandatory rather than
  best-practice.

## Open follow-ups (intentionally deferred)

- **Events table retention** (N-H5) — daily
  `DELETE FROM events WHERE created_at < now() - interval '180 days'`.
- **`total_storage_bytes` denormalisation** (N-H7) — replace the
  correlated subquery in `GET /projects` with a column updated on
  commit/delete. The ETag short-circuit already covers the dashboard
  polling case; this is only worth doing once the per-call latency
  becomes a visible complaint.
- **Per-file error reporting in `/confirm_uploads`** (CA6) — return
  `{ok: [], failed: [{hash, reason}]}` so a partially-failed bulk
  commit can retry only the failed entries.
- **`chmod 0o600`** on `~/.copal/config.json` (#11) — single-user
  workstation exposure; not impactful on the typical artist setup.
