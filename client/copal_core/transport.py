import os
import sys
import requests
from requests.exceptions import ConnectionError, Timeout, RequestException
from .config import FILER_BASE

# Global Session for Connection Pooling (Critical for Threading speed)
session = requests.Session()

def upload_file(file_path, file_hash):
    """
    Uploads a file to SeaweedFS.
    Returns: (Success: bool, Result: str)
    Result is either the FID (on success) or Error Message (on fail).
    """
    """Uploads a file to SeaweedFS Filer (force SSD)."""
    file_size = os.path.getsize(file_path)
    target_url = f"{FILER_BASE}/blobs/{file_hash}?disk=ssd"
    

    try:
        with open(file_path, 'rb') as f:
            headers = {'Expect': '100-continue', 'Content-Length': str(file_size)}
            # Use the global session
            response = session.put(target_url, data=f, headers=headers, timeout=None)
            
            if response.status_code in [200, 201]:
                return True, f"/blobs/{file_hash}"
            else:
                return False, f"HTTP {response.status_code}: {response.text}"

    except ConnectionError:
        return False, "Connection Refused"
    except Exception as e:
        return False, str(e)




def download_file(fid, local_path, expected_size):
    """
    Downloads a file. 
    Returns: (Success: bool, Message: str)
    """
    url = f"{FILER_BASE}{fid}"

    try:
        # stream=True is vital for large files
        with session.get(url, stream=True, timeout=None) as r:
            if r.status_code == 404:
                return False, "Not Found on Server"
            
            r.raise_for_status()
            
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): 
                    f.write(chunk)
        return True, "Success"

    except ConnectionError:
        return False, "Connection Refused"
    except Exception as e:
        return False, str(e)