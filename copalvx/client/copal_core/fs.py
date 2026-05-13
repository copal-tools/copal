import os
import sys
import hashlib
import fnmatch
import json
import re
import subprocess

_verbose = sys.stdout.isatty()

# Match transport.CHUNK_SIZE — both code paths read whole files for SHA-256
# so the chunk size dominates throughput on big files.
CHUNK_SIZE = 1024 * 1024

# Cache file lives inside the project's hidden .copal dir, alongside state.json.
_HASH_CACHE_BASENAME = "hash_cache.json"


# ── Hash cache ────────────────────────────────────────────────────────────────
# A persistent {rel_path: {mtime_ns, size, hash}} map. Pulls and pushes both
# re-scan every file in the project; without a cache that re-hashes ~MB/s of
# data on every operation. Invalidation is "mtime + size" — same heuristic as
# rsync / git status / make. False negatives (cached hash returned for a file
# the user actually changed) require the user to change the mtime back to its
# original value, which is essentially impossible by accident.


def _hash_cache_path(root_dir):
    return os.path.join(root_dir, ".copal", _HASH_CACHE_BASENAME)


def _load_hash_cache(root_dir):
    p = _hash_cache_path(root_dir)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_hash_cache(root_dir, cache):
    copal_dir = os.path.join(root_dir, ".copal")
    try:
        os.makedirs(copal_dir, exist_ok=True)
        path = _hash_cache_path(root_dir)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, path)
    except Exception as e:
        if _verbose:
            print(f"⚠️  Could not write hash cache: {e}")


def calculate_hash(filepath):
    """Calculates SHA256 hash of a local file (no cache)."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest()
    except FileNotFoundError:
        return None


def _stat_or_none(filepath):
    try:
        return os.stat(filepath)
    except OSError:
        return None


def hash_with_cache(filepath, cache, rel_path):
    """SHA-256 with a (mtime_ns, size) memoised lookup in ``cache``.

    The cache dict is mutated in place. Callers are responsible for persisting
    it with ``_save_hash_cache`` after the scan completes.
    """
    st = _stat_or_none(filepath)
    if st is None:
        return None
    size = st.st_size
    mtime_ns = st.st_mtime_ns

    entry = cache.get(rel_path)
    if entry and entry.get("size") == size and entry.get("mtime_ns") == mtime_ns:
        h = entry.get("hash")
        if h:
            return h

    h = calculate_hash(filepath)
    if h is not None:
        cache[rel_path] = {"size": size, "mtime_ns": mtime_ns, "hash": h}
    return h


# ── .copalignore rule compilation ──────────────────────────────────────────────


class IgnoreRules:
    """Compiled .copalignore matcher.

    Walks the rule list once at construction time and partitions each rule into
    one of three buckets so :meth:`matches` is O(rules per file) but with each
    comparison reduced to a hash lookup or a single regex match against a
    pre-translated pattern.
    """

    __slots__ = ("exact_names", "folder_segments", "name_regex")

    def __init__(self, raw_rules):
        self.exact_names = set()
        self.folder_segments = set()
        wildcards = []

        for rule in raw_rules:
            if not rule:
                continue
            if rule.endswith("/"):
                # Folder rule — matches any path component equal to the prefix.
                self.folder_segments.add(rule.rstrip("/"))
                continue
            if any(ch in rule for ch in "*?["):
                wildcards.append(fnmatch.translate(rule))
            else:
                self.exact_names.add(rule)

        if wildcards:
            self.name_regex = re.compile("|".join(f"(?:{p})" for p in wildcards))
        else:
            self.name_regex = None

    def matches(self, abs_path, rel_path):
        filename = os.path.basename(abs_path)
        if filename in self.exact_names:
            return True
        if self.name_regex is not None and self.name_regex.match(filename):
            return True
        if self.folder_segments:
            for segment in rel_path.split(os.sep):
                if segment in self.folder_segments:
                    return True
        return False


def load_ignore_rules(root_dir):
    """Reads .copalignore (plus built-in defaults) and returns a compiled matcher."""
    rules = {".DS_Store", "Thumbs.db", ".git", "__pycache__", ".venv", ".copal"}

    ignore_path = os.path.join(root_dir, ".copalignore")
    if os.path.exists(ignore_path):
        try:
            with open(ignore_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        rules.add(line)
            if _verbose:
                print("ℹ️  Loaded .copalignore rules.")
        except Exception as e:
            print(f"⚠️  Error reading .copalignore: {e}")

    return IgnoreRules(rules)


def should_ignore(path, root_dir, rules):
    """Back-compat shim: accepts either a compiled :class:`IgnoreRules` or a raw set."""
    rel_path = os.path.relpath(path, root_dir)
    if isinstance(rules, IgnoreRules):
        return rules.matches(path, rel_path)

    # Legacy raw-set path — kept so external callers don't break.
    filename = os.path.basename(path)
    for rule in rules:
        if rule == filename:
            return True
        if fnmatch.fnmatch(filename, rule):
            return True
        if rule.endswith("/") and rule.strip("/") in rel_path.split(os.sep):
            return True
    return False


def scan_directory(root_dir):
    """Recursively scans a directory with .copalignore + hash cache support."""
    file_list = []
    if _verbose:
        print(f"🔍 Scanning directory: {root_dir}")

    ignore_rules = load_ignore_rules(root_dir)
    cache = _load_hash_cache(root_dir)
    cache_seen = set()
    cache_dirty = False

    for root, dirs, files in os.walk(root_dir):
        # Filter directories in-place (os.walk requires this for pruning)
        for i in range(len(dirs) - 1, -1, -1):
            full_dir_path = os.path.join(root, dirs[i])
            rel_dir = os.path.relpath(full_dir_path, root_dir)
            if ignore_rules.matches(full_dir_path, rel_dir):
                del dirs[i]

        for file in files:
            full_path = os.path.join(root, file)
            rel_disk = os.path.relpath(full_path, root_dir)

            if ignore_rules.matches(full_path, rel_disk):
                continue

            rel_path = rel_disk.replace("\\", "/")

            try:
                st = _stat_or_none(full_path)
                if st is None:
                    print(f"⚠️ Skipping inaccessible file: {full_path}")
                    continue
                file_size = st.st_size
                mtime_ns = st.st_mtime_ns

                entry = cache.get(rel_path)
                if entry and entry.get("size") == file_size and entry.get("mtime_ns") == mtime_ns:
                    file_hash = entry["hash"]
                else:
                    file_hash = calculate_hash(full_path)
                    if file_hash is not None:
                        cache[rel_path] = {"size": file_size, "mtime_ns": mtime_ns, "hash": file_hash}
                        cache_dirty = True

                cache_seen.add(rel_path)
                file_list.append({
                    "path": rel_path,
                    "hash": file_hash,
                    "size": file_size,
                    "full_local_path": full_path,
                })
            except OSError:
                print(f"⚠️ Skipping inaccessible file: {full_path}")

    # Drop cache entries for files that no longer exist
    stale = [k for k in cache if k not in cache_seen]
    if stale:
        for k in stale:
            del cache[k]
        cache_dirty = True

    if cache_dirty:
        _save_hash_cache(root_dir, cache)

    return file_list


def load_local_state(root_dir):
    """
    Reads .copal/state.json to find previous project info.
    Returns dict or None.
    """
    state_file = os.path.join(root_dir, ".copal", "state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_local_state(root_dir, project_id, last_tag):
    """
    Saves project info to .copal/state.json so we remember it next time.
    """
    copal_dir = os.path.join(root_dir, ".copal")
    os.makedirs(copal_dir, exist_ok=True)

    state_file = os.path.join(copal_dir, "state.json")
    data = {
        "project_id": project_id,
        "last_tag": last_tag,
        "last_updated": os.path.getmtime(root_dir)
    }

    try:
        with open(state_file, "w") as f:
            json.dump(data, f, indent=4)
        # Hide the folder on Windows
        if os.name == 'nt':
            subprocess.run(['attrib', '+h', str(copal_dir)], capture_output=True)
    except Exception as e:
        print(f"⚠️  Could not save local state: {e}")
