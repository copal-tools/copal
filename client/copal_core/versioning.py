import re

def ensure_prefix(tag):
    """Ensures tag starts with 'v'. e.g. '1.0' -> 'v1.0'."""
    tag = tag.strip()
    if not tag: return ""
    if not tag.lower().startswith("v"):
        return f"v{tag}"
    return tag

def increment_tag(last_tag):
    """
    Auto-increments a version tag.
    v1.0 -> v1.1
    v1.9 -> v1.10
    v1 -> v2
    """
    if not last_tag:
        return "v1.0"
        
    # Regex to find the last number in the string
    match = re.search(r"(\d+)$", last_tag)
    if match:
        number = int(match.group(1))
        new_number = number + 1
        # Replace the old number with new number
        return last_tag[:match.start()] + str(new_number)
    
    # Fallback if weird format
    return f"{last_tag}.1"

def validate_push_tag(new_tag, existing_tags):
    """
    Checks if tag is valid (not a duplicate).
    Returns (Valid: Bool, Message: Str)
    """
    clean_tag = ensure_prefix(new_tag)
    if clean_tag in existing_tags:
        return False, f"Version '{clean_tag}' already exists on server."
    return True, ""