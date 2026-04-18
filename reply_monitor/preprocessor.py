"""Pure-Python pre-processing for GDPR data exports.

Extracts structural metadata from export files (ZIP, JSON, CSV) without
any LLM call. Produces a PreprocessResult that the schema builder uses
to construct an enriched LLM prompt focused on understanding, not parsing.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

_MAX_SAMPLE_BYTES = 3000
_MAX_FILES = 25
_MAX_JSON_PARSE_BYTES = 1024 * 1024  # 1 MB
_MAX_JSON_KEYS = 50

_RE_TWITTER_JS = re.compile(r"^window\.YTD\.\w+\.part\d+\s*=\s*", re.MULTILINE)


@dataclass
class FileMeta:
    """Metadata about one file in the export."""
    filename: str
    size_bytes: int
    file_type: str
    record_count: int = 0
    headers: list[str] = field(default_factory=list)
    json_keys: list[str] = field(default_factory=list)
    json_structure: str = ""


@dataclass
class PreprocessResult:
    """Structural metadata extracted from an export without LLM."""
    total_files: int = 0
    formats_found: list[str] = field(default_factory=list)
    folder_tree: dict[str, list[str]] = field(default_factory=dict)
    file_metas: list[FileMeta] = field(default_factory=list)
    file_samples: list[dict] = field(default_factory=list)
    total_records_estimate: int = 0


def _unwrap_twitter_js(content: str) -> str:
    """Strip Twitter JS wrapper if present, returning raw JSON."""
    m = _RE_TWITTER_JS.match(content)
    if m:
        return content[m.end():]
    return content


def _analyze_json(raw: bytes, filename: str) -> tuple[FileMeta, str]:
    """Analyze a JSON (or Twitter JS) file, returning (FileMeta, sample_str)."""
    ext = Path(filename).suffix.lstrip(".").lower()
    size_bytes = len(raw)
    file_type = "js" if ext == "js" else "json"

    # Decode for parsing (up to 4x sample bytes) and for sample display
    try:
        text = raw[:_MAX_JSON_PARSE_BYTES].decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"[preprocessor] JSON decode failed for {filename}: {exc}")
        text = ""

    if ext == "js":
        text = _unwrap_twitter_js(text)

    sample = text[:_MAX_SAMPLE_BYTES]

    meta = FileMeta(filename=filename, size_bytes=size_bytes, file_type=file_type)

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return meta, sample

    if isinstance(parsed, dict):
        meta.json_structure = "object"
        meta.json_keys = list(parsed.keys())[:_MAX_JSON_KEYS]
    elif isinstance(parsed, list):
        meta.json_structure = "array"
        meta.record_count = len(parsed)
        # Extract keys from first element if it's a dict
        if parsed and isinstance(parsed[0], dict):
            meta.json_keys = list(parsed[0].keys())[:_MAX_JSON_KEYS]

    return meta, sample


def _analyze_csv(raw: bytes, filename: str) -> tuple[FileMeta, str]:
    """Analyze a CSV or TSV file, returning (FileMeta, sample_str)."""
    ext = Path(filename).suffix.lstrip(".").lower()
    size_bytes = len(raw)
    file_type = ext  # "csv" or "tsv"

    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"[preprocessor] CSV decode failed for {filename}: {exc}")
        text = ""

    sample = text[:_MAX_SAMPLE_BYTES]
    meta = FileMeta(filename=filename, size_bytes=size_bytes, file_type=file_type)

    if not text.strip():
        return meta, sample

    # Detect delimiter using Sniffer, fall back to comma
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t|;")
        reader = csv.reader(io.StringIO(text), dialect)
    except csv.Error:
        reader = csv.reader(io.StringIO(text))

    headers = next(reader, None)
    if headers is None:
        return meta, sample

    meta.headers = list(headers)
    # Count data rows with generator to avoid loading full file into memory
    data_rows = sum(1 for _ in reader)
    meta.record_count = max(0, data_rows)

    return meta, sample


def preprocess(file_path: Path) -> PreprocessResult:
    """Pre-process an export file and extract structural metadata."""
    ext = file_path.suffix.lstrip(".").lower()

    if ext == "zip":
        return _preprocess_zip(file_path)
    elif ext in ("json", "js", "csv", "txt", "tsv"):
        return _preprocess_single(file_path, ext)
    else:
        return PreprocessResult()


def _preprocess_zip(file_path: Path) -> PreprocessResult:
    """Extract folder tree, file stats, and content samples from a ZIP."""
    result = PreprocessResult()
    try:
        with zipfile.ZipFile(file_path) as zf:
            entries = [e for e in zf.infolist() if not e.is_dir()]
            result.total_files = len(entries)

            formats: set[str] = set()
            folder_tree: dict[str, list[str]] = {}

            for info in entries:
                ext = Path(info.filename).suffix.lstrip(".").lower()
                if ext:
                    formats.add(ext)

                parts = Path(info.filename).parts
                if len(parts) > 1:
                    folder = str(Path(*parts[:-1]))
                else:
                    folder = "."
                folder_tree.setdefault(folder, []).append(parts[-1])

            result.formats_found = sorted(formats)
            result.folder_tree = folder_tree

            # Analyze file contents (up to _MAX_FILES)
            total_records = 0
            for info in entries[:_MAX_FILES]:
                ext = Path(info.filename).suffix.lstrip(".").lower()
                fname = Path(info.filename).name

                try:
                    raw = zf.read(info.filename)
                except Exception as exc:
                    print(f"[preprocessor] ZIP entry read failed for {info.filename}: {exc}")
                    continue

                if ext in ("json", "js"):
                    meta, sample = _analyze_json(raw, fname)
                elif ext in ("csv", "tsv"):
                    meta, sample = _analyze_csv(raw, fname)
                elif ext in ("txt", "xml"):
                    meta = FileMeta(
                        filename=fname,
                        size_bytes=len(raw),
                        file_type=ext,
                    )
                    sample = raw[:_MAX_SAMPLE_BYTES].decode("utf-8", errors="replace")
                else:
                    continue

                result.file_metas.append(meta)
                result.file_samples.append({"filename": fname, "content": sample})
                total_records += meta.record_count

            result.total_records_estimate = total_records

    except Exception as exc:
        print(f"[preprocessor] ZIP processing failed for {file_path}: {exc}")

    return result


def build_context_summary(result: PreprocessResult) -> str:
    """Format PreprocessResult into a structured text block for the LLM prompt."""
    lines: list[str] = []

    # Stats header
    lines.append(f"EXPORT OVERVIEW: {result.total_files} files, "
                 f"formats: {', '.join(result.formats_found) or 'unknown'}")
    if result.total_records_estimate > 0:
        lines.append(f"Total data records (estimated): {result.total_records_estimate:,}")
    lines.append("")

    # Folder structure
    if result.folder_tree and result.folder_tree != {".": []}:
        lines.append("FOLDER STRUCTURE:")
        for folder, files in sorted(result.folder_tree.items()):
            prefix = f"  {folder}/" if folder != "." else "  ./"
            lines.append(f"{prefix} ({len(files)} files)")
            for fname in sorted(files)[:10]:
                lines.append(f"    {fname}")
            if len(files) > 10:
                lines.append(f"    ... and {len(files) - 10} more")
        lines.append("")

    # Per-file metadata
    if result.file_metas:
        lines.append("FILE ANALYSIS:")
        for meta in result.file_metas:
            parts = [meta.filename]
            if meta.json_structure:
                parts.append(f"structure={meta.json_structure}")
            if meta.record_count > 0:
                parts.append(f"{meta.record_count:,} records")
            if meta.json_keys:
                keys_str = ", ".join(meta.json_keys[:15])
                if len(meta.json_keys) > 15:
                    keys_str += f" ... (+{len(meta.json_keys) - 15} more)"
                parts.append(f"keys=[{keys_str}]")
            if meta.headers:
                hdrs_str = ", ".join(meta.headers[:15])
                if len(meta.headers) > 15:
                    hdrs_str += f" ... (+{len(meta.headers) - 15} more)"
                parts.append(f"columns=[{hdrs_str}]")
            lines.append(f"  {' | '.join(parts)}")
        lines.append("")

    return "\n".join(lines)


def _preprocess_single(file_path: Path, ext: str) -> PreprocessResult:
    """Pre-process a single JSON/CSV/TXT file."""
    result = PreprocessResult(total_files=1, formats_found=[ext])
    result.folder_tree = {".": [file_path.name]}

    try:
        raw = file_path.read_bytes()
    except Exception as exc:
        print(f"[preprocessor] file read failed for {file_path}: {exc}")
        return result

    if ext in ("json", "js"):
        meta, sample = _analyze_json(raw, file_path.name)
    elif ext in ("csv", "tsv"):
        meta, sample = _analyze_csv(raw, file_path.name)
    elif ext in ("txt", "xml"):
        meta = FileMeta(
            filename=file_path.name,
            size_bytes=len(raw),
            file_type=ext,
        )
        sample = raw[:_MAX_SAMPLE_BYTES].decode("utf-8", errors="replace")
    else:
        return result

    result.file_metas.append(meta)
    result.file_samples.append({"filename": file_path.name, "content": sample})
    result.total_records_estimate = meta.record_count

    return result
