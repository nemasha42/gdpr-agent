"""Download and catalog email attachments received in SAR replies."""

from __future__ import annotations

import base64
import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from reply_monitor.models import AttachmentCatalog, FileEntry

_RECEIVED_DIR = Path(__file__).parent.parent / "user_data" / "received"

# Extensions that are never data exports (images, web assets, etc.)
_NON_DATA_EXTS = {"png", "jpg", "jpeg", "gif", "ico", "svg", "webp", "bmp", "pdf"}

# Filename patterns → data category names
_CATEGORY_HINTS: list[tuple[str, str]] = [
    (r"location|geo|maps",          "Location"),
    (r"search|query|browsi",        "Search History"),
    (r"purchase|order|transaction|payment|buy", "Purchase History"),
    (r"ad|advertis|targeting",      "Advertising"),
    (r"watch|view|video|youtube",   "Watch History"),
    (r"profile|account|user",       "Profile Data"),
    (r"message|chat|mail|email",    "Communications"),
    (r"contact",                    "Contacts"),
    (r"activity|event|log",         "Activity Log"),
    (r"device|hardware|sensor",     "Device Data"),
    (r"app|application|install",    "App Usage"),
    (r"social|friend|follow|like",  "Social Graph"),
    (r"post|comment|review",        "Content"),
    (r"health|fitness|step",        "Health Data"),
    (r"financial|bank|card",        "Financial Data"),
]


def handle_attachment(
    service: Any,
    message_id: str,
    part: dict,
    domain: str,
) -> AttachmentCatalog | None:
    """Download one email attachment and return a catalog of its contents.

    Args:
        service:    Authenticated Gmail readonly service
        message_id: Gmail message ID (for attachment download)
        part:       Attachment part dict from fetcher (filename, attachmentId, size)
        domain:     Company domain — used to set download directory

    Returns:
        AttachmentCatalog or None if download failed
    """
    attachment_id = part.get("attachmentId", "")
    filename = part.get("filename", "attachment")
    size = part.get("size", 0)

    # Skip non-data file types (images, web assets) — never actual data exports
    ext_check = Path(filename).suffix.lstrip(".").lower()
    if ext_check in _NON_DATA_EXTS:
        return None

    # Download raw bytes
    try:
        resp = service.users().messages().attachments().get(
            userId="me",
            messageId=message_id,
            id=attachment_id,
        ).execute()
        data = base64.urlsafe_b64decode(resp["data"])
    except Exception:
        return None

    # Save to disk
    save_dir = _RECEIVED_DIR / domain
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / filename
    save_path.write_bytes(data)

    ext = Path(filename).suffix.lstrip(".").lower()
    files: list[FileEntry] = []
    categories: list[str] = []

    if ext == "zip":
        files, categories = _catalog_zip(data, filename)
    elif ext == "json":
        files, categories = _catalog_json(data, filename, len(data))
    elif ext == "csv":
        files, categories = _catalog_csv(data, filename, len(data))
    else:
        files = [FileEntry(filename=filename, size_bytes=len(data), file_type=ext)]
        categories = _guess_categories_from_filename(filename)

    return AttachmentCatalog(
        path=str(save_path),
        size_bytes=len(data),
        file_type=ext,
        files=files,
        categories=sorted(set(categories)),
    )


# ---------------------------------------------------------------------------
# Format-specific catalogers
# ---------------------------------------------------------------------------


def _catalog_zip(data: bytes, outer_filename: str) -> tuple[list[FileEntry], list[str]]:
    """List all files in a ZIP archive and infer data categories."""
    files: list[FileEntry] = []
    categories: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                ext = Path(info.filename).suffix.lstrip(".").lower()
                files.append(FileEntry(
                    filename=info.filename,
                    size_bytes=info.file_size,
                    file_type=ext,
                ))
                categories.extend(_guess_categories_from_filename(info.filename))
    except zipfile.BadZipFile:
        files = [FileEntry(filename=outer_filename, size_bytes=len(data), file_type="zip")]
    return files, categories


def _catalog_json(data: bytes, filename: str, size: int) -> tuple[list[FileEntry], list[str]]:
    """Use top-level JSON keys as data category hints."""
    categories: list[str] = []
    try:
        parsed = json.loads(data.decode("utf-8", errors="replace"))
        if isinstance(parsed, dict):
            for key in parsed.keys():
                categories.extend(_guess_categories_from_filename(key))
        elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            for key in parsed[0].keys():
                categories.extend(_guess_categories_from_filename(key))
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    ext = Path(filename).suffix.lstrip(".").lower()
    return [FileEntry(filename=filename, size_bytes=size, file_type=ext)], categories


def _catalog_csv(data: bytes, filename: str, size: int) -> tuple[list[FileEntry], list[str]]:
    """Use CSV column headers as data category hints."""
    categories: list[str] = _guess_categories_from_filename(filename)
    try:
        text = data.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        headers = next(reader, [])
        for h in headers:
            categories.extend(_guess_categories_from_filename(h))
    except Exception:
        pass
    ext = Path(filename).suffix.lstrip(".").lower()
    return [FileEntry(filename=filename, size_bytes=size, file_type=ext)], categories


# ---------------------------------------------------------------------------
# Category inference
# ---------------------------------------------------------------------------


def _guess_categories_from_filename(name: str) -> list[str]:
    """Return zero or more data category strings based on filename/key."""
    import re
    name_lower = name.lower()
    matched = []
    for pattern, category in _CATEGORY_HINTS:
        if re.search(pattern, name_lower):
            matched.append(category)
    return matched
