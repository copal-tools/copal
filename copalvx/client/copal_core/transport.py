import os
import hashlib
import time
import requests
from urllib.parse import quote
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, Timeout, RequestException
from .config import FILER_BASE

# Connection timeout: fail fast if server unreachable.
# Read timeout: per-chunk inactivity watchdog. If no byte arrives for 120 s we
# abort the attempt — large file transfers are fine because the timeout resets
# each time data arrives. Without this a half-open TCP connection would hang a
# worker thread forever and eventually deadlock the whole pull.
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 120
TRANSFER_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# 1 MiB chunks roughly double SHA-256 throughput on SSDs vs the old 8 KiB
# chunks, with no measurable cost on slower disks.
CHUNK_SIZE = 1024 * 1024

# Connection pool sized for SyncEngine's max worker count.  Bumping the
# default beyond the typical thread count keeps "extra" threads (e.g. when
# a future caller raises max_threads) from blocking on pool checkout.
POOL_SIZE = int(os.getenv("COPAL_HTTP_POOL", "32"))

session = requests.Session()
adapter = HTTPAdapter(
    pool_connections=POOL_SIZE,
    pool_maxsize=POOL_SIZE,
    max_retries=0,
)
session.mount("http://", adapter)
session.mount("https://", adapter)


def _safe_fid_url(fid: str) -> str:
    """Compose a SeaweedFS URL with the FID percent-quoted (defense-in-depth).

    The server is trusted, but quoting prevents a stray FID-shaped string with
    a space or ``?`` from breaking the URL and degrading errors into confusing
    HTTP-level failures.  The forward slash that prefixes a FID is preserved.
    """
    return f"{FILER_BASE}{quote(fid, safe='/?:&=')}"


def _is_retryable(success, result_str, status_code=None):
    if not success:
        if "Connection" in result_str or "Timeout" in result_str:
            return True
        if status_code and status_code in RETRYABLE_STATUS_CODES:
            return True
    return False


def upload_file(file_path, file_hash):
    """Uploads a file to SeaweedFS Filer (force SSD).
    Returns: (Success: bool, Result: str)
    Result is either the FID (on success) or Error Message (on fail).
    """
    file_size = os.path.getsize(file_path)
    target_url = f"{FILER_BASE}/blobs/{file_hash}?disk=ssd"

    last_result = (False, "No attempts made")

    for attempt in range(MAX_RETRIES):
        try:
            with open(file_path, 'rb') as f:
                headers = {'Expect': '100-continue', 'Content-Length': str(file_size)}
                response = session.put(target_url, data=f, headers=headers, timeout=TRANSFER_TIMEOUT)

                if response.status_code in [200, 201]:
                    return True, f"/blobs/{file_hash}"

                last_result = (False, f"HTTP {response.status_code}: {response.text}")
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    return last_result

        except ConnectionError:
            last_result = (False, "Connection Refused")
        except Timeout:
            last_result = (False, "Connection Timed Out")
        except Exception as e:
            return False, str(e)

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF[attempt])

    return last_result


def _hash_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b''):
            h.update(chunk)
    return h.hexdigest()


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def download_file(fid, local_path, expected_size, expected_hash):
    """Downloads a file from SeaweedFS and verifies its hash after writing.

    Writes go to ``<local_path>.partial`` and only `os.replace` onto the final
    name once the hash check passes. A crash or hash mismatch leaves the .partial
    file (which the caller can ignore) and never a half-written final file.

    Returns: (Success: bool, Message: str)
    """
    url = _safe_fid_url(fid)
    partial_path = local_path + ".partial"

    last_result = (False, "No attempts made")

    for attempt in range(MAX_RETRIES):
        try:
            with session.get(url, stream=True, timeout=TRANSFER_TIMEOUT) as r:
                if r.status_code == 404:
                    return False, "Not Found on Server"

                if r.status_code in RETRYABLE_STATUS_CODES:
                    last_result = (False, f"HTTP {r.status_code}")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_BACKOFF[attempt])
                    continue

                r.raise_for_status()

                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(partial_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        f.write(chunk)

            actual_hash = _hash_file(partial_path)
            if actual_hash != expected_hash:
                _safe_remove(partial_path)
                last_result = (False, "Hash mismatch — file corrupted in transit")
                # Treat as a retryable failure — fall through to sleep-and-retry
                # at the bottom of the loop rather than giving up immediately.
            else:
                # Atomic publish: only after a verified write does the target
                # path appear.  ``os.replace`` is atomic on POSIX and Windows
                # (across the same filesystem) and overwrites any existing file.
                os.replace(partial_path, local_path)
                return True, "Success"

        except ConnectionError:
            _safe_remove(partial_path)
            last_result = (False, "Connection Refused")
        except Timeout:
            _safe_remove(partial_path)
            last_result = (False, "Connection Timed Out")
        except Exception as e:
            _safe_remove(partial_path)
            return False, str(e)

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF[attempt])

    _safe_remove(partial_path)
    return last_result
