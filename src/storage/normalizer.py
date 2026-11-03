"""Data cleansing, canonicalization, and extraction utilities for ingestion hygiene."""

import re
from typing import Optional, Any
from urllib.parse import urlparse, parse_qs


# Standard YouTube 11-character video ID regex pattern
YOUTUBE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")

# Google Drive file ID pattern (typically 28 to 44 alphanumeric/hyphen/underscore characters)
DRIVE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{15,50}$")


def normalize_email(val: Any) -> str:
    """Sanitize and canonicalize email address.
    
    Strips whitespace and enforces lowercase.
    """
    if val is None:
        return ""
    clean = str(val).strip().lower()
    # Remove any zero-width or non-printable unicode characters
    clean = re.sub(r"[\u200b\u200e\u200f\ufeff]", "", clean)
    return clean


def extract_youtube_id(url_or_id: Any) -> Optional[str]:
    """Extract canonical 11-character YouTube video ID from various URL formats or raw ID.
    
    Supports:
      - Raw 11-char ID: 'RzW17zhV4Nw'
      - Standard URL: 'https://www.youtube.com/watch?v=RzW17zhV4Nw'
      - Short URL: 'https://youtu.be/RzW17zhV4Nw'
      - Embed URL: 'https://www.youtube.com/embed/RzW17zhV4Nw'
      - URL with query params: 'https://www.youtube.com/watch?v=RzW17zhV4Nw&t=10s'
    """
    if not url_or_id:
        return None
    raw = str(url_or_id).strip()

    # Case 1: Already a valid 11-character YouTube ID
    if YOUTUBE_ID_PATTERN.match(raw):
        return raw

    # Case 2: Parse as URL
    try:
        parsed = urlparse(raw)
        # Handle youtu.be/<id>
        if "youtu.be" in parsed.netloc:
            path_part = parsed.path.strip("/").split("/")[0]
            if YOUTUBE_ID_PATTERN.match(path_part):
                return path_part

        # Handle youtube.com/watch?v=<id>
        if "youtube.com" in parsed.netloc:
            query_params = parse_qs(parsed.query)
            if "v" in query_params and query_params["v"]:
                vid = query_params["v"][0]
                if YOUTUBE_ID_PATTERN.match(vid):
                    return vid
            
            # Handle youtube.com/embed/<id> or /v/<id>
            path_parts = [p for p in parsed.path.split("/") if p]
            if len(path_parts) >= 2 and path_parts[0] in ("embed", "v"):
                vid = path_parts[1]
                if YOUTUBE_ID_PATTERN.match(vid):
                    return vid
    except Exception:
        pass

    # Regex search fallback for embedded 11-char ID in string
    match = re.search(r"[a-zA-Z0-9_-]{11}", raw)
    if match and ("watch?v=" in raw or "youtu.be/" in raw):
        return match.group(0)

    # Return raw cleaned string if no pattern matched (fallback)
    return raw if raw else None


def extract_drive_file_id(url_or_id: Any) -> Optional[str]:
    """Extract canonical Google Drive file ID from URL or raw ID.
    
    Supports:
      - Raw file ID: '1ovAQ1N19bqlFgjhSuC8Aw4wnvSKzbTsFxt5qtOYzaY4' or 'drive_file_101'
      - File URL: 'https://drive.google.com/file/d/1ovAQ1N19bqlFgjhSuC8Aw4wnvSKzbTsFxt5qtOYzaY4/view'
      - Open URL: 'https://drive.google.com/open?id=1ovAQ1N19bqlFgjhSuC8Aw4wnvSKzbTsFxt5qtOYzaY4'
    """
    if not url_or_id:
        return None
    raw = str(url_or_id).strip()

    # Case 1: drive.google.com/file/d/<id>/...
    match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", raw)
    if match:
        return match.group(1)

    # Case 2: drive.google.com/open?id=<id>
    try:
        parsed = urlparse(raw)
        if "drive.google.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            if "id" in qs and qs["id"]:
                return qs["id"][0]
    except Exception:
        pass

    # Case 3: Already an ID token
    return raw


def normalize_status(val: Any, default: str = "submitted") -> str:
    """Normalize lifecycle statuses to lowercase snake_case."""
    if not val:
        return default
    clean = str(val).strip().lower().replace("-", "_").replace(" ", "_")
    return clean


def clean_text(val: Any) -> Optional[str]:
    """Clean free text: strip whitespace and convert empty string to None."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None
