"""LLM-powered schema analysis for GDPR data exports.

Analyzes the contents of a data export ZIP (or JSON/CSV) and uses Claude
to infer a structured schema in dataowners.org card format:

  {
    "categories": [
      {
        "name": "Category Name",
        "description": "What personal data this category contains",
        "fields": [
          {"name": "...", "type": "string|number|boolean|date|array|object",
           "example": "...", "description": "Short description of what this field means to the user"}
        ]
      }
    ],
    "services": [
      {"name": "Job Search Platform", "description": "Search and apply for jobs by title, location, and company"}
    ],
    "export_meta": {
      "format": "ZIP",
      "delivery": "Download link sent via email",
      "timeline": "Provided within 30 days"
    }
  }
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

_MAX_SAMPLE_BYTES = 3000   # per file, sent to LLM
_MAX_FILES = 25            # cap number of files sampled


def build_schema(file_path: Path, api_key: str) -> dict:
    """Analyze a data export file and return an LLM-inferred schema.

    Args:
        file_path: Path to the downloaded file (ZIP, JSON, CSV)
        api_key:   Anthropic API key

    Returns:
        Dict with keys: categories (list), services (list), export_meta (dict).
        Returns empty dict on failure or missing api_key.
    """
    if not api_key:
        return {}

    ext = file_path.suffix.lstrip(".").lower()

    if ext == "zip":
        samples = _sample_zip(file_path)
    elif ext in ("json", "csv", "txt", "tsv"):
        content = _read_sample(file_path, _MAX_SAMPLE_BYTES)
        samples = [{"filename": file_path.name, "content": content}] if content else []
    else:
        return {}

    if not samples:
        return {}

    return _call_llm(samples, api_key)


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------


def _sample_zip(file_path: Path) -> list[dict]:
    """Open ZIP and sample readable text content from each data file."""
    samples: list[dict] = []
    try:
        with zipfile.ZipFile(file_path) as zf:
            entries = [e for e in zf.infolist() if not e.is_dir()]
            for info in entries[:_MAX_FILES]:
                ext = Path(info.filename).suffix.lstrip(".").lower()
                if ext not in ("json", "csv", "txt", "tsv", "xml"):
                    continue
                try:
                    raw = zf.read(info.filename)
                    content = raw[:_MAX_SAMPLE_BYTES].decode("utf-8", errors="replace")
                    if content.strip():
                        samples.append({"filename": info.filename, "content": content})
                except Exception:
                    continue
    except Exception:
        pass
    return samples


def _read_sample(file_path: Path, max_bytes: int) -> str:
    try:
        raw = file_path.read_bytes()
        return raw[:max_bytes].decode("utf-8", errors="replace")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _call_llm(samples: list[dict], api_key: str) -> dict:
    """Send file samples to Claude and parse the returned schema JSON."""
    try:
        import anthropic
    except ImportError:
        return {}

    file_blocks = "\n".join(
        f"=== {s['filename']} ===\n{s['content'][:2000]}"
        for s in samples
    )

    prompt = f"""You are analyzing a GDPR Subject Access Request (SAR) data export.
The files below are samples from the export package.

{file_blocks}

Produce a structured data schema that describes the personal data held.
Return a JSON object — no markdown, no explanation, only valid JSON.

Schema format:
{{
  "categories": [
    {{
      "name": "Human-readable category name (e.g. Profile, Job Applications, Reviews)",
      "description": "One sentence: what personal data this category contains",
      "fields": [
        {{
          "name": "field_name_from_data",
          "type": "string | number | boolean | date | array | object",
          "example": "actual example value from the file",
          "description": "Short description of what this field means to the user"
        }}
      ]
    }}
  ],
  "services": [
    {{
      "name": "Name of one product/service the company offers",
      "description": "One sentence describing what this service does for the user"
    }}
  ],
  "export_meta": {{
    "format": "ZIP | JSON | CSV | etc.",
    "delivery": "How the data was delivered (e.g. Download link sent via email)",
    "timeline": "How long it took or the statutory period (e.g. Provided within 30 days)"
  }}
}}

Rules:
- Use clear, human-readable category names
- One category per logical data group (not one per file)
- Include only the 5-8 most important / personal fields per category
- Use real values from the samples as examples
- services: list the distinct products/features the company's export reveals
- export_meta: infer format from file extension; delivery and timeline from context
- Return ONLY the JSON object"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown code fence if model adds one
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        if isinstance(result, dict):
            return result
        # Legacy: if model returns a list (old prompt format), wrap it
        if isinstance(result, list):
            return {"categories": result, "services": [], "export_meta": {}}
    except Exception as exc:
        print(f"[schema_builder] LLM call failed: {exc}")
    return {}
