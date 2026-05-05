import os
import hashlib
import time
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, Timeout, RequestException
from .config import FILER_BASE

# Connection timeout: fail fast if server unreachable.
# Read timeout: None — large file transfers can take as long as they need.
CONNECT_TIMEOUT = 30
TRANSFER_TIMEOUT = (CONNECT_TIMEOUT, None)

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Global Session with pool sized to match SyncEngine thread count.
session = requests.Session()
adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=0)
session.mount("http://", adapter)
session.mount("https://", adapter)


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
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def download_file(fid, local_path, expected_size, expected_hash):
    """Downloads a file from SeaweedFS and verifies its hash after writing.
    Returns: (Success: bool, Message: str)
    """
    url = f"{FILER_BASE}{fid}"

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
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            actual_hash = _hash_file(local_path)
            if actual_hash != expected_hash:
                os.remove(local_path)
                return False, "Hash mismatch — file corrupted in transit"

            return True, "Success"

        except ConnectionError:
            last_result = (False, "Connection Refused")
        except Timeout:
            last_result = (False, "Connection Timed Out")
        except Exception as e:
            return False, str(e)

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF[attempt])

    return last_result
